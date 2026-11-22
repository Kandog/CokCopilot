"""MCP (Model Context Protocol) client: connects to configured servers and exposes their tools.

Servers are configured in ``mcp.json`` in the data dir, in the same
``mcpServers`` shape Claude Desktop / VS Code use, so configs can be pasted in:

    {
      "mcpServers": {
        "filesystem": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "C:/notes"]},
        "remote":     {"type": "http", "url": "https://example.com/mcp", "headers": {"Authorization": "Bearer ..."}}
      }
    }

Extra per-server keys: ``disabled`` (bool), ``disabledTools`` (list of tool
names hidden from the model) and ``autoApprove`` (tools that run without asking).

Each server runs in its own long-lived task (the SDK's transports must be
entered and exited in the same task); tool calls go through its session.
"""

from __future__ import annotations

import asyncio
import json
import re
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import config

CONNECT_TIMEOUT = 60  # first `npx -y ...` run downloads the package
CALL_TIMEOUT = 180
MAX_RESULT_CHARS = 20_000
LOG_DIR_NAME = "logs"

STATUS_DISABLED = "disabled"
STATUS_CONNECTING = "connecting"
STATUS_CONNECTED = "connected"
STATUS_ERROR = "error"


class MCPConfigError(ValueError):
    pass


def sanitize_server_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", name.strip()).strip("-")[:40]


def normalize_server_config(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate one server entry and fill in ``type``."""
    if not isinstance(raw, dict):
        raise MCPConfigError("server config must be an object")
    cfg = dict(raw)
    kind = str(cfg.get("type") or cfg.get("transport") or "").lower().replace("_", "-")
    if not kind:
        kind = "http" if cfg.get("url") else "stdio"
    if kind in ("streamable-http", "streamablehttp", "streamable"):
        kind = "http"
    if kind not in ("stdio", "http", "sse"):
        raise MCPConfigError(f"unknown transport type {kind!r} (use stdio, http or sse)")
    cfg["type"] = kind
    cfg.pop("transport", None)
    if kind == "stdio":
        if not str(cfg.get("command") or "").strip():
            raise MCPConfigError("a stdio server needs a command")
        args = cfg.get("args") or []
        if not isinstance(args, list):
            raise MCPConfigError("args must be a list of strings")
        cfg["args"] = [str(a) for a in args]
    else:
        url = str(cfg.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            raise MCPConfigError("an http/sse server needs an http(s) url")
        cfg["url"] = url
    for key in ("env", "headers"):
        if key in cfg and not isinstance(cfg[key], dict):
            raise MCPConfigError(f"{key} must be an object of strings")
    for key in ("disabledTools", "autoApprove"):
        cfg[key] = [str(t) for t in cfg.get(key) or []]
    cfg["disabled"] = bool(cfg.get("disabled", False))
    return cfg


@dataclass
class ToolInfo:
    name: str
    description: str
    input_schema: dict[str, Any]
    title: str = ""
    read_only: bool = False


@dataclass
class ServerRuntime:
    name: str
    cfg: dict[str, Any]
    status: str = STATUS_DISABLED
    error: str = ""
    tools: list[ToolInfo] = field(default_factory=list)
    server_label: str = ""
    instructions: str = ""
    client: Any = None
    _task: asyncio.Task | None = None
    _stop: asyncio.Event = field(default_factory=asyncio.Event)
    _ready: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def log_path(self) -> Path:
        return config.DATA_DIR / LOG_DIR_NAME / f"mcp-{sanitize_server_name(self.name) or 'server'}.log"

    def to_dict(self) -> dict[str, Any]:
        disabled_tools = set(self.cfg.get("disabledTools") or [])
        auto = set(self.cfg.get("autoApprove") or [])
        return {
            "name": self.name,
            "config": self.cfg,
            "status": self.status,
            "error": self.error,
            "server_label": self.server_label,
            "tools": [
                {
                    "name": t.name,
                    "title": t.title,
                    "description": t.description,
                    "enabled": t.name not in disabled_tools,
                    "auto_approve": t.name in auto,
                    "read_only": t.read_only,
                    "input_schema": t.input_schema,
                }
                for t in self.tools
            ],
            "log_tail": self.log_tail(),
        }

    def log_tail(self, lines: int = 30) -> str:
        try:
            text = self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return "\n".join(text.splitlines()[-lines:])


def _error_text(e: BaseException) -> str:
    while isinstance(e, BaseExceptionGroup) and e.exceptions:
        e = e.exceptions[0]
    msg = str(e).strip()
    if isinstance(e, FileNotFoundError):
        return f"command not found ({msg or e})"
    return msg or type(e).__name__


def _as_dict(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True, exclude_none=True)
    return value


def result_to_text(result: Any) -> tuple[str, bool]:
    """Flatten a CallToolResult into text for the model. Returns (text, is_error)."""
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        kind = getattr(block, "type", "")
        if kind == "text":
            parts.append(block.text)
        elif kind in ("image", "audio"):
            parts.append(f"[{kind} returned ({getattr(block, 'mime_type', '') or 'binary'}), not shown to the model]")
        elif kind == "resource":
            res = block.resource
            text = getattr(res, "text", None)
            parts.append(text if text is not None else f"[binary resource {getattr(res, 'uri', '')}]")
        elif kind == "resource_link":
            parts.append(f"[resource link: {getattr(block, 'name', '')} {getattr(block, 'uri', '')}]")
        else:
            parts.append(json.dumps(_as_dict(block), ensure_ascii=False))
    structured = getattr(result, "structured_content", None)
    if not parts and structured is not None:
        parts.append(json.dumps(structured, ensure_ascii=False, indent=2))
    text = "\n".join(parts).strip() or "(no output)"
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + f"\n…[truncated {len(text) - MAX_RESULT_CHARS} characters]"
    return text, bool(getattr(result, "is_error", False))


class MCPManager:
    def __init__(self, path: Path | None = None):
        self.path = path or config.MCP_FILE
        self.servers: dict[str, ServerRuntime] = {}
        self._lock = asyncio.Lock()

    # -- config --

    def _read_config(self) -> dict[str, dict[str, Any]]:
        data = config.read_json(self.path) or {}
        out = {}
        for name, raw in (data.get("mcpServers") or {}).items():
            try:
                out[name] = normalize_server_config(raw)
            except MCPConfigError as e:
                rt = ServerRuntime(name, raw if isinstance(raw, dict) else {})
                rt.status, rt.error = STATUS_ERROR, f"invalid config: {e}"
                self.servers[name] = rt
        return out

    def _write_config(self) -> None:
        config.write_json(self.path, {"mcpServers": {n: s.cfg for n, s in self.servers.items()}})

    # -- lifecycle --

    async def start_all(self) -> None:
        for name, cfg in self._read_config().items():
            self.servers[name] = ServerRuntime(name, cfg)
        await asyncio.gather(*(self._start(rt) for rt in list(self.servers.values()) if rt.cfg.get("type")),
                             return_exceptions=True)

    async def stop_all(self) -> None:
        await asyncio.gather(*(self._stop(rt) for rt in list(self.servers.values())), return_exceptions=True)

    async def _start(self, rt: ServerRuntime) -> None:
        await self._stop(rt)
        if rt.cfg.get("disabled"):
            rt.status, rt.error, rt.tools = STATUS_DISABLED, "", []
            return
        rt.status, rt.error = STATUS_CONNECTING, ""
        rt._stop, rt._ready = asyncio.Event(), asyncio.Event()
        rt._task = asyncio.create_task(self._run(rt), name=f"mcp-{rt.name}")
        try:
            await asyncio.wait_for(rt._ready.wait(), timeout=CONNECT_TIMEOUT)
        except TimeoutError:
            rt.error = f"timed out after {CONNECT_TIMEOUT}s while connecting"
            await self._stop(rt)
            rt.status = STATUS_ERROR

    async def _stop(self, rt: ServerRuntime) -> None:
        task, rt._task = rt._task, None
        if task and not task.done():
            rt._stop.set()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=8)
            except (TimeoutError, Exception):
                task.cancel()
                try:
                    await task
                except BaseException:
                    pass
        rt.client = None

    async def _run(self, rt: ServerRuntime) -> None:
        from mcp import Client, StdioServerParameters
        from mcp.client.sse import sse_client
        from mcp.client.stdio import stdio_client
        from mcp.client.streamable_http import streamable_http_client
        from mcp.shared._httpx_utils import create_mcp_http_client

        cfg = rt.cfg
        try:
            async with AsyncExitStack() as stack:
                if cfg["type"] == "stdio":
                    rt.log_path.parent.mkdir(parents=True, exist_ok=True)
                    errlog = stack.enter_context(rt.log_path.open("w", encoding="utf-8", errors="replace"))
                    params = StdioServerParameters(
                        command=cfg["command"],
                        args=cfg.get("args") or [],
                        env={**_default_env(), **{k: str(v) for k, v in (cfg.get("env") or {}).items()}},
                        cwd=cfg.get("cwd") or None,
                        encoding_error_handler="replace",
                    )
                    transport = stdio_client(params, errlog=errlog)
                elif cfg["type"] == "sse":
                    transport = sse_client(cfg["url"], headers=cfg.get("headers") or None)
                else:
                    http = await stack.enter_async_context(create_mcp_http_client(headers=cfg.get("headers") or None))
                    transport = streamable_http_client(cfg["url"], http_client=http)
                client = await stack.enter_async_context(
                    Client(transport, read_timeout_seconds=CALL_TIMEOUT, raise_exceptions=False)
                )
                rt.tools = await _list_all_tools(client)
                info = client.server_info
                rt.server_label = f"{info.name} {info.version}".strip() if info else ""
                rt.instructions = getattr(client, "instructions", None) or ""
                rt.client = client
                rt.status, rt.error = STATUS_CONNECTED, ""
                rt._ready.set()
                await rt._stop.wait()
        except asyncio.CancelledError:
            raise
        except BaseException as e:  # noqa: BLE001 - surface any transport/handshake failure
            rt.error = _error_text(e)
            tail = rt.log_tail(5) if cfg.get("type") == "stdio" else ""
            if tail and tail not in rt.error:
                rt.error += f"\n{tail}"
            rt.status = STATUS_ERROR
        finally:
            rt.client = None
            if rt.status == STATUS_CONNECTED:
                rt.status = STATUS_DISABLED if rt.cfg.get("disabled") else STATUS_ERROR
                if rt.status == STATUS_ERROR and not rt.error:
                    rt.error = "disconnected"
            rt._ready.set()

    # -- management API --

    def list(self) -> list[dict[str, Any]]:
        return [rt.to_dict() for rt in sorted(self.servers.values(), key=lambda r: r.name.lower())]

    def get(self, name: str) -> ServerRuntime | None:
        return self.servers.get(name)

    async def upsert(self, name: str, raw: dict[str, Any], old_name: str | None = None) -> ServerRuntime:
        name = sanitize_server_name(name)
        if not name:
            raise MCPConfigError("give the server a name (letters, digits, - or _)")
        cfg = normalize_server_config(raw)
        async with self._lock:
            # Check the new name before touching the old server, so a clash loses nothing.
            if name in self.servers and old_name != name:
                raise MCPConfigError(f'a server named "{name}" already exists')
            if old_name and old_name != name and old_name in self.servers:
                await self._stop(self.servers.pop(old_name))
            rt = self.servers.get(name)
            if rt:
                await self._stop(rt)
                rt.cfg = cfg
            else:
                rt = self.servers[name] = ServerRuntime(name, cfg)
            self._write_config()
        await self._start(rt)
        return rt

    async def import_json(self, text: str) -> list[str]:
        """Add servers from pasted JSON: ``{"mcpServers": {...}}``, ``{"servers": {...}}`` or ``{name: cfg}``."""
        try:
            data = json.loads(text)
        except ValueError as e:
            raise MCPConfigError(f"not valid JSON ({e})") from e
        if not isinstance(data, dict):
            raise MCPConfigError("expected a JSON object")
        servers = data.get("mcpServers") or data.get("servers") or data
        if not isinstance(servers, dict) or not servers:
            raise MCPConfigError("no servers found in the JSON")
        if "command" in servers or "url" in servers:
            raise MCPConfigError('wrap a single server in a name, e.g. {"my-server": {...}}')
        for cfg in servers.values():
            normalize_server_config(cfg)  # validate all before adding any
        added = []
        for n, cfg in servers.items():
            rt = await self.upsert(n, cfg, old_name=sanitize_server_name(n))
            added.append(rt.name)
        return added

    async def remove(self, name: str) -> None:
        async with self._lock:
            rt = self.servers.pop(name, None)
            if rt:
                await self._stop(rt)
                self._write_config()

    async def restart(self, name: str) -> ServerRuntime | None:
        rt = self.servers.get(name)
        if rt and rt.cfg.get("type"):
            await self._start(rt)
        return rt

    async def set_enabled(self, name: str, enabled: bool) -> ServerRuntime | None:
        rt = self.servers.get(name)
        if not rt:
            return None
        rt.cfg["disabled"] = not enabled
        self._write_config()
        await self._start(rt)
        return rt

    def set_tool_flags(self, name: str, tool: str, enabled: bool | None = None,
                       auto_approve: bool | None = None) -> ServerRuntime | None:
        rt = self.servers.get(name)
        if not rt:
            return None
        for key, flag, present_means in (("disabledTools", enabled, False), ("autoApprove", auto_approve, True)):
            if flag is None:
                continue
            items = [t for t in rt.cfg.get(key) or [] if t != tool]
            if flag == present_means:
                items.append(tool)
            rt.cfg[key] = items
        self._write_config()
        return rt

    # -- tools for the model --

    def openai_tools(self) -> tuple[list[dict[str, Any]], dict[str, tuple[str, str]]]:
        """Function-calling schemas for every enabled tool, and fn-name -> (server, tool)."""
        tools: list[dict[str, Any]] = []
        mapping: dict[str, tuple[str, str]] = {}
        for rt in sorted(self.servers.values(), key=lambda r: r.name):
            if rt.status != STATUS_CONNECTED:
                continue
            disabled = set(rt.cfg.get("disabledTools") or [])
            for t in rt.tools:
                if t.name in disabled:
                    continue
                fn = _function_name(rt.name, t.name, mapping)
                mapping[fn] = (rt.name, t.name)
                desc = t.description or t.title or t.name
                tools.append({
                    "type": "function",
                    "function": {
                        "name": fn,
                        "description": f"[{rt.name}] {desc}"[:1024],
                        "parameters": _clean_schema(t.input_schema),
                    },
                })
        return tools, mapping

    def instructions(self) -> str:
        blocks = [
            f'<mcp_server name="{rt.name}">\n{rt.instructions.strip()}\n</mcp_server>'
            for rt in self.servers.values()
            if rt.status == STATUS_CONNECTED and rt.instructions.strip()
        ]
        return ("Instructions from connected tool servers:\n" + "\n".join(blocks)) if blocks else ""

    def is_auto_approved(self, server: str, tool: str) -> bool:
        rt = self.servers.get(server)
        return bool(rt and tool in (rt.cfg.get("autoApprove") or []))

    async def call_tool(self, server: str, tool: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        rt = self.servers.get(server)
        if not rt or rt.status != STATUS_CONNECTED or rt.client is None:
            return f'MCP server "{server}" is not connected.', True
        try:
            result = await asyncio.wait_for(rt.client.call_tool(tool, arguments), timeout=CALL_TIMEOUT + 5)
        except TimeoutError:
            return f"Tool call timed out after {CALL_TIMEOUT}s.", True
        except Exception as e:  # noqa: BLE001
            return f"Tool call failed: {_error_text(e)}", True
        return result_to_text(result)


async def _list_all_tools(client: Any) -> list[ToolInfo]:
    tools: list[ToolInfo] = []
    cursor = None
    for _ in range(50):
        page = await client.list_tools(cursor=cursor)
        for t in page.tools:
            ann = getattr(t, "annotations", None)
            tools.append(ToolInfo(
                name=t.name,
                description=(t.description or "").strip(),
                input_schema=_as_dict(t.input_schema) or {},
                title=getattr(t, "title", None) or (getattr(ann, "title", None) if ann else None) or "",
                read_only=bool(getattr(ann, "read_only_hint", False)) if ann else False,
            ))
        cursor = getattr(page, "next_cursor", None)
        if not cursor:
            break
    return tools


def _function_name(server: str, tool: str, taken: dict[str, Any]) -> str:
    """OpenAI function names: ^[A-Za-z0-9_-]{1,64}$, unique."""
    base = re.sub(r"[^A-Za-z0-9_-]", "_", f"{server}__{tool}")[:64]
    name, i = base, 2
    while name in taken:
        suffix = f"_{i}"
        name = base[: 64 - len(suffix)] + suffix
        i += 1
    return name


def _clean_schema(schema: dict[str, Any]) -> dict[str, Any]:
    s = dict(schema or {})
    if s.get("type") != "object":
        s = {"type": "object", "properties": {}, **({"description": s["description"]} if "description" in s else {})}
    s.setdefault("properties", {})
    s.pop("$schema", None)
    return s


def _default_env() -> dict[str, str]:
    from mcp.client.stdio import get_default_environment

    return get_default_environment()
