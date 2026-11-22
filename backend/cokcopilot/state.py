"""Process-wide application state shared by the API routes."""

from __future__ import annotations

import asyncio
import dataclasses
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import config
from .auth import LOCAL_USER, User, UserStore
from .chat import Stream
from .groups import Group, GroupStore
from .knowledge import KnowledgeBase, SyncResult, _is_link, watch_folder
from .mcp_manager import MCPManager
from .memory import MemoryStore
from .models import Settings
from .skills import SkillStore
from .sso import SSOStore
from .usage import UsageLog

# Settings each user chooses for themselves in server mode. Everything else
# (API connection, sampling, knowledge folder, MCP approval policy) is set by an admin.
PREF_FIELDS = ("theme", "default_model", "rag_enabled", "memory_enabled", "mcp_enabled", "disable_thinking")
LINKED_FOLDER_PREF = "linked_knowledge_folder"
LINKED_POLL_SECONDS = 60  # shares don't always report changes: rescan linked folders this often
LINKED_MAX_FILES = 5000


def _within(p: Path, root: Path) -> bool:
    try:
        return p.is_relative_to(root.resolve())
    except (OSError, RuntimeError):
        return False


def _apply_link_limits(kb: KnowledgeBase, linked: bool) -> None:
    kb.no_links = linked
    kb.max_files = LINKED_MAX_FILES if linked else None


# Never sent to the browser.
SECRET_FIELDS = ("api_key",)


@dataclass
class UserCtx:
    """Everything a request needs to act as one user."""

    user: User
    settings: Settings  # effective: server settings + this user's preferences
    memory: MemoryStore
    convos_dir: Path
    prefs_path: Path | None  # None in local mode (all settings live in config.json)
    kb: KnowledgeBase | None = None  # the user's own documents (server mode only)
    # Set when acting inside a group: chats live in the group's folder and replies
    # search the group's documents (plus the team's) instead of the user's own.
    group: Group | None = None
    group_kb: KnowledgeBase | None = None


@dataclass
class AppState:
    settings: Settings
    skills: SkillStore
    kb: KnowledgeBase
    mcp: MCPManager
    mode: str = "local"  # "local" (single user, no login) | "server" (users + access keys)
    users: UserStore | None = None
    groups: GroupStore | None = None
    sso: SSOStore | None = None
    usage: UsageLog = field(default_factory=UsageLog)
    streams: dict[str, Stream] = field(default_factory=dict)
    last_sync: str = ""
    _memories: dict[str, MemoryStore] = field(default_factory=dict)
    _kbs: dict[str, KnowledgeBase] = field(default_factory=dict)
    _stop: asyncio.Event = field(default_factory=asyncio.Event)
    _tasks: list[asyncio.Task] = field(default_factory=list)
    _watchers: dict[str, asyncio.Task] = field(default_factory=dict)
    _background: set[asyncio.Task] = field(default_factory=set)

    @classmethod
    def load(cls) -> AppState:
        config.ensure_dirs()
        settings = config.load_settings()
        mode = "server" if os.environ.get("COKCOPILOT_MODE") == "server" else "local"
        state = cls(
            settings=settings,
            skills=SkillStore(),
            kb=KnowledgeBase(config.knowledge_folder(settings)),
            mcp=MCPManager(),
            mode=mode,
            users=UserStore() if mode == "server" else None,
            groups=GroupStore() if mode == "server" else None,
            sso=SSOStore() if mode == "server" else None,
        )
        state.kb.listeners.append(state._on_sync)
        # On a server, deleted files would land in the service account's Recycle Bin.
        state.kb.use_trash = state.skills.use_trash = not state.is_server
        return state

    def spawn(self, coro) -> asyncio.Task:
        """Run ``coro`` in the background, keeping a reference so it isn't garbage-collected."""
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        return task

    @property
    def is_server(self) -> bool:
        return self.mode == "server"

    # -- users --

    def sso_on(self) -> bool:
        return bool(self.sso and self.sso.reload().ready)

    def key_login_allowed(self, user: User) -> bool:
        """While SSO is on, access keys work for admins (a way back in) and, if allowed, for everyone."""
        return user.is_admin or not self.sso_on() or self.sso.config.allow_access_keys

    def user_dir(self, user: User) -> Path:
        return config.DATA_DIR if user.id == LOCAL_USER.id else config.DATA_DIR / "users" / user.id

    def ctx(self, user: User) -> UserCtx:
        if not self.is_server:
            return UserCtx(user, self.settings, self._memory(user, config.MEMORY_FILE),
                           config.CONVERSATIONS_DIR, None)
        d = self.user_dir(user)
        prefs_path = d / "prefs.json"
        prefs = config.read_json(prefs_path) or {}
        merged = {**self.settings.to_dict(), **{k: v for k, v in prefs.items() if k in PREF_FIELDS}}
        return UserCtx(user, Settings.from_dict(merged), self._memory(user, d / "memory.json"),
                       d / "conversations", prefs_path, self._personal_kb(user))

    # -- groups --

    def group_ctx(self, ctx: UserCtx, group: Group) -> UserCtx:
        """``ctx`` acting inside ``group`` (the caller has checked membership)."""
        return dataclasses.replace(ctx, convos_dir=self.groups.dir(group.id) / "conversations",
                                   group=group, group_kb=self.group_kb(group.id))

    def group_kb(self, group_id: str) -> KnowledgeBase:
        key = f"group:{group_id}"
        if key not in self._kbs:
            g = self.groups.get(group_id)
            self._kbs[key] = self._make_kb(self.groups.dir(group_id), g.knowledge_folder if g else "")
            self._watch_if_linked(key)
        return self._kbs[key]

    def delete_group_data(self, group_id: str) -> None:
        self._unwatch(f"group:{group_id}")
        self._kbs.pop(f"group:{group_id}", None)
        shutil.rmtree(self.groups.dir(group_id), ignore_errors=True)

    def _personal_kb(self, user: User) -> KnowledgeBase:
        """A user's own knowledge base: files they upload (in their data folder), or a linked shared folder."""
        if user.id not in self._kbs:
            self._kbs[user.id] = self._make_kb(self.user_dir(user), self.linked_folder(user))
            self._watch_if_linked(user.id)
        return self._kbs[user.id]

    @staticmethod
    def _make_kb(d: Path, linked: str) -> KnowledgeBase:
        kb = KnowledgeBase(Path(linked) if linked else d / "knowledge_files", d / "knowledge_index.json",
                           d / "knowledge")
        kb.use_trash = False  # never touch a server account's Recycle Bin
        _apply_link_limits(kb, bool(linked))
        return kb

    # -- linked shared folders (server mode) --

    def knowledge_roots(self) -> list[Path]:
        return [Path(line.strip()) for line in self.settings.knowledge_roots.splitlines() if line.strip()]

    def linked_folder(self, user: User) -> str:
        prefs = config.read_json(self.user_dir(user) / "prefs.json") or {}
        return str(prefs.get(LINKED_FOLDER_PREF) or "")

    def check_linkable(self, folder: str) -> Path:
        """``folder`` resolved, if users may link it; raises ValueError with a reason otherwise."""
        roots = self.knowledge_roots()
        if not roots:
            raise ValueError("Your admin hasn't allowed any shared folders yet (Settings → Knowledge → Shared folder roots).")
        raw = folder.strip().strip('"')
        if not raw:
            raise ValueError("Enter a folder path")
        try:
            p = Path(raw).resolve()
        except (OSError, RuntimeError) as e:
            raise ValueError(f"{raw}: not a valid folder path ({e})") from e
        if not any(_within(p, r) for r in roots):
            raise ValueError(f"{raw} isn't inside a folder your admin allows: " + "; ".join(str(r) for r in roots))
        data = config.DATA_DIR.resolve()
        if _within(p, data) or _within(data, p):
            raise ValueError("That folder holds CokCopilot's own data and can't be used")
        if not p.is_dir():
            raise ValueError(f"The server can't open {raw}. Check the path and that the server's account can read it.")
        return p

    def browse(self, folder: str) -> dict[str, Any]:
        """Subfolders of ``folder`` (or the allowed roots), for the folder picker."""
        if not folder.strip():
            return {"path": "", "parent": None,
                    "dirs": [{"name": str(r), "path": str(r)} for r in self.knowledge_roots()]}
        p = self.check_linkable(folder)
        try:
            names = sorted((e.name for e in os.scandir(p)
                            if e.is_dir() and not e.name.startswith((".", "$")) and not _is_link(Path(e.path))),
                           key=str.lower)
        except OSError as e:
            raise ValueError(f"The server can't list {p} ({e.strerror or e})") from e
        at_root = any(p == r.resolve() for r in self.knowledge_roots())
        return {"path": str(p), "parent": "" if at_root else str(p.parent),
                "dirs": [{"name": n, "path": str(p / n)} for n in names[:500]]}

    async def link_folder(self, folder: str, user: User, group_id: str = "") -> None:
        """Point ``user``'s own knowledge base, or (``group_id``, owner only) a group's, at ``folder``.

        "" unlinks: back to the documents uploaded in the app (they were kept)."""
        key = f"group:{group_id}" if group_id else user.id
        path = str(self.check_linkable(folder)) if folder else ""
        if not group_id:
            prefs_path = self.user_dir(user) / "prefs.json"
            config.write_json(prefs_path, {**(config.read_json(prefs_path) or {}), LINKED_FOLDER_PREF: path})
            kb, base = self._personal_kb(user), self.user_dir(user)
        else:
            self.groups.set_folder(group_id, user.id, path)  # raises GroupError unless user owns it
            kb, base = self.group_kb(group_id), self.groups.dir(group_id)
        self._unwatch(key)
        _apply_link_limits(kb, bool(path))
        await kb.set_folder(Path(path) if path else base / "knowledge_files")
        if path:
            self._watch_if_linked(key)
        else:
            self.spawn(kb.sync(self.settings))

    def _watch_if_linked(self, key: str) -> None:
        kb = self._kbs.get(key)
        if not kb or not kb.no_links or key in self._watchers:
            return
        kb.watched = True
        try:
            task = asyncio.get_running_loop().create_task(
                watch_folder(kb, lambda: self.settings, self._stop, LINKED_POLL_SECONDS))
        except RuntimeError:  # no event loop (e.g. CLI use): searches sync on demand instead
            kb.watched = False
            return
        self._watchers[key] = task

    def _unwatch(self, key: str) -> None:
        task = self._watchers.pop(key, None)
        if task:
            task.cancel()
        if key in self._kbs:
            self._kbs[key].watched = False

    def _start_linked_watchers(self) -> None:
        """At startup: index and watch every linked folder, so new files are found without anyone asking."""
        if not self.is_server:
            return
        for u in self.users.list():
            if not u.disabled and self.linked_folder(u):
                self._personal_kb(u)
        for g in self.groups.all():
            if g.knowledge_folder:
                self.group_kb(g.id)

    def _memory(self, user: User, path: Path) -> MemoryStore:
        if user.id not in self._memories:
            self._memories[user.id] = MemoryStore(path)
        return self._memories[user.id]

    def public_settings(self, ctx: UserCtx) -> dict[str, Any]:
        d = ctx.settings.to_dict()
        d["api_key_set"] = bool(self.settings.api_key)
        for k in SECRET_FIELDS:
            d[k] = ""
        if ctx.user.is_admin:
            d["effective_knowledge_folder"] = str(self.kb.folder)
        else:
            d["knowledge_folder"] = ""
        return d

    async def save_settings(self, ctx: UserCtx, body: dict[str, Any]) -> None:
        """Preferences go to the user's prefs (server mode); the rest needs an admin."""
        body = dict(body)
        if not body.get("api_key"):
            body.pop("api_key", None)  # empty = keep the saved key
        known = set(Settings().to_dict())
        body = {k: v for k, v in body.items() if k in known}
        if ctx.prefs_path is not None:
            prefs = {k: body.pop(k) for k in list(body) if k in PREF_FIELDS}
            if prefs:
                current = config.read_json(ctx.prefs_path) or {}
                config.write_json(ctx.prefs_path, {**current, **prefs})
        if body:
            if not ctx.user.is_admin:
                raise PermissionError("only an admin can change server settings")
            await self.update_settings(Settings.from_dict({**self.settings.to_dict(), **body}))

    def _on_sync(self, result: SyncResult) -> None:
        self.last_sync = "Knowledge folder changed" if result.removed == ["*"] else result.summary()

    # -- lifecycle --

    async def start(self) -> None:
        self._tasks.append(asyncio.create_task(watch_folder(self.kb, lambda: self.settings, self._stop)))
        self._tasks.append(asyncio.create_task(self.mcp.start_all()))
        self._start_linked_watchers()

    async def shutdown(self) -> None:
        self._stop.set()
        for s in list(self.streams.values()):
            if s.task and not s.task.done():
                s.task.cancel()
        await self.mcp.stop_all()
        watchers = list(self._watchers.values())
        for t in self._tasks + watchers:
            t.cancel()
        await asyncio.gather(*self._tasks, *watchers, return_exceptions=True)

    async def update_settings(self, new: Settings) -> None:
        old_folder = config.knowledge_folder(self.settings)
        self.settings = new
        config.save_settings(new)
        folder = config.knowledge_folder(new)
        if folder != old_folder:
            await self.kb.set_folder(folder)
            self.spawn(self.kb.sync(new))
