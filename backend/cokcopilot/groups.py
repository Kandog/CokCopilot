"""Groups on a team server: users who share chats and a knowledge base.

- Any user can create a group and becomes its owner.
- The owner invites other users by name. An invited user sees the invite and
  accepts (becomes a member) or declines it.
- Every member can read all of the group's chats and add or remove documents in
  the group's knowledge base. Only the owner renames or deletes the group,
  invites people, or removes members; any member can leave (except the owner).

Groups are stored in ``groups.json`` in the data dir; each group's chats and
documents live in ``groups/<id>/``.
"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import config
from .models import new_id, now_iso

MAX_NAME = 80


class GroupError(Exception):
    pass


@dataclass
class Group:
    id: str
    name: str
    owner_id: str
    members: list[str] = field(default_factory=list)  # user ids, owner included
    invites: list[str] = field(default_factory=list)  # user ids invited but not yet joined
    created_at: str = ""
    # A shared folder linked as the group's knowledge base (empty = documents uploaded in the app).
    knowledge_folder: str = ""

    def is_member(self, user_id: str) -> bool:
        return user_id in self.members

    def is_owner(self, user_id: str) -> bool:
        return user_id == self.owner_id


class GroupStore:
    def __init__(self, path: Path | None = None):
        self.path = path or config.DATA_DIR / "groups.json"
        self._lock = threading.Lock()
        self.groups: list[Group] = []
        self._mtime = -1.0
        self._reload()

    def dir(self, group_id: str) -> Path:
        return config.DATA_DIR / "groups" / group_id

    def _reload(self) -> None:
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            mtime = 0.0
        if mtime == self._mtime:
            return
        self._mtime = mtime
        data = config.read_json(self.path) or {}
        out = []
        for g in data.get("groups", []):
            try:
                out.append(Group(**{k: g[k] for k in Group.__dataclass_fields__ if k in g}))
            except TypeError:
                continue
        self.groups = out

    def _save(self) -> None:
        config.write_json(self.path, {"groups": [asdict(g) for g in self.groups]})
        try:
            self._mtime = self.path.stat().st_mtime
        except OSError:
            pass

    # -- queries --

    def get(self, group_id: str) -> Group | None:
        self._reload()
        return next((g for g in self.groups if g.id == group_id), None)

    def all(self) -> list[Group]:
        self._reload()
        return list(self.groups)

    def for_user(self, user_id: str) -> list[Group]:
        self._reload()
        return [g for g in self.groups if g.is_member(user_id)]

    def invites_for(self, user_id: str) -> list[Group]:
        self._reload()
        return [g for g in self.groups if user_id in g.invites]

    # -- changes --

    def create(self, name: str, owner_id: str) -> Group:
        name = _clean_name(name)
        with self._lock:
            self._reload()
            g = Group(new_id(), name, owner_id, [owner_id], [], now_iso())
            self.groups.append(g)
            self._save()
        return g

    def rename(self, group_id: str, user_id: str, name: str) -> Group:
        name = _clean_name(name)
        with self._lock:
            g = self._owned(group_id, user_id)
            g.name = name
            self._save()
            return g

    def set_folder(self, group_id: str, user_id: str, folder: str) -> Group:
        with self._lock:
            g = self._owned(group_id, user_id)
            g.knowledge_folder = folder
            self._save()
            return g

    def delete(self, group_id: str, user_id: str) -> None:
        with self._lock:
            self._owned(group_id, user_id)
            self.groups = [x for x in self.groups if x.id != group_id]
            self._save()

    def invite(self, group_id: str, user_id: str, invitee_id: str) -> Group:
        with self._lock:
            g = self._owned(group_id, user_id)
            if invitee_id in g.members:
                raise GroupError("they're already in this group")
            if invitee_id not in g.invites:
                g.invites.append(invitee_id)
                self._save()
            return g

    def cancel_invite(self, group_id: str, user_id: str, invitee_id: str) -> Group:
        with self._lock:
            g = self._owned(group_id, user_id)
            g.invites = [x for x in g.invites if x != invitee_id]
            self._save()
            return g

    def respond(self, group_id: str, user_id: str, accept: bool) -> Group:
        with self._lock:
            g = self._require(group_id)
            if user_id not in g.invites:
                raise GroupError("there's no invitation to this group for you")
            g.invites.remove(user_id)
            if accept and user_id not in g.members:
                g.members.append(user_id)
            self._save()
            return g

    def remove_member(self, group_id: str, user_id: str, member_id: str) -> Group:
        """The owner removes someone, or a member leaves (``member_id == user_id``)."""
        with self._lock:
            g = self._require(group_id)
            if not g.is_member(user_id):
                raise GroupError("group not found")
            if member_id != user_id and not g.is_owner(user_id):
                raise GroupError("only the group's owner can remove members")
            if g.is_owner(member_id):
                raise GroupError("the owner can't leave the group; delete it instead")
            g.members = [x for x in g.members if x != member_id]
            self._save()
            return g

    def forget_user(self, user_id: str) -> None:
        """A user account was removed: drop them everywhere and hand on groups they owned."""
        with self._lock:
            self._reload()
            changed = False
            for g in self.groups:
                if user_id in g.members or user_id in g.invites:
                    g.members = [x for x in g.members if x != user_id]
                    g.invites = [x for x in g.invites if x != user_id]
                    changed = True
                if g.owner_id == user_id and g.members:
                    g.owner_id = g.members[0]  # longest-standing member
            if changed:
                self._save()

    def _require(self, group_id: str) -> Group:
        self._reload()
        g = next((x for x in self.groups if x.id == group_id), None)
        if not g:
            raise GroupError("group not found")
        return g

    def _owned(self, group_id: str, user_id: str) -> Group:
        g = self._require(group_id)
        if not g.is_member(user_id):
            raise GroupError("group not found")
        if not g.is_owner(user_id):
            raise GroupError("only the group's owner can do this")
        return g


def _clean_name(name: str) -> str:
    name = " ".join(str(name or "").split())
    if not name:
        raise GroupError("give the group a name")
    return name[:MAX_NAME]
