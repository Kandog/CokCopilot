"""Data models. JSON shapes match the earlier versions so old data loads unchanged."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from typing import Any

NEW_CONVERSATION_TITLE = "New Conversation"


def new_id() -> str:
    """12 hex chars, like ``uuid4().hex[:12]``."""
    return uuid.uuid4().hex[:12]


def now_iso() -> str:
    return datetime.now().isoformat(timespec="microseconds")


@dataclass
class ToolCallRecord:
    """One MCP tool call made while producing an assistant reply (shown in the UI)."""

    id: str
    server: str
    tool: str
    arguments: dict[str, Any] = field(default_factory=dict)
    result: str = ""
    # done | error | denied
    status: str = "done"


@dataclass
class Message:
    role: str  # user | assistant | system
    content: str
    timestamp: str = field(default_factory=now_iso)
    id: str = field(default_factory=new_id)
    images: list[str] = field(default_factory=list)  # base64
    document_texts: list[str] = field(default_factory=list)
    # Skill invoked with `/name`; stays in effect for the rest of the conversation.
    skill: str = ""
    # Display names of attached files (new in v3; optional).
    attachments: list[str] = field(default_factory=list)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Message:
        return cls(
            role=d["role"],
            content=d.get("content") or "",
            timestamp=d.get("timestamp") or now_iso(),
            id=d.get("id") or new_id(),
            images=list(d.get("images") or []),
            document_texts=list(d.get("document_texts") or []),
            skill=d.get("skill") or "",
            attachments=list(d.get("attachments") or []),
            tool_calls=[ToolCallRecord(**t) for t in d.get("tool_calls") or []],
        )

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Keep files lean and compatible: drop empty optional fields.
        for key in ("skill", "attachments", "tool_calls"):
            if not d[key]:
                del d[key]
        return d


@dataclass
class Conversation:
    title: str = NEW_CONVERSATION_TITLE
    system_prompt: str = ""  # empty = global default
    model: str = ""  # empty = settings default
    messages: list[Message] = field(default_factory=list)
    id: str = field(default_factory=new_id)
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    # Group chats (server mode): which group, and who started it. Empty for personal chats.
    group_id: str = ""
    user_id: str = ""
    user_name: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Conversation:
        return cls(
            title=d.get("title") or NEW_CONVERSATION_TITLE,
            system_prompt=d.get("system_prompt") or "",
            model=d.get("model") or "",
            messages=[Message.from_dict(m) for m in d.get("messages") or []],
            id=d["id"],
            created_at=d.get("created_at") or now_iso(),
            updated_at=d.get("updated_at") or now_iso(),
            group_id=d.get("group_id") or "",
            user_id=d.get("user_id") or "",
            user_name=d.get("user_name") or "",
        )

    def _group_fields(self) -> dict[str, Any]:
        if not self.group_id:
            return {}
        return {"group_id": self.group_id, "user_id": self.user_id, "user_name": self.user_name}

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "system_prompt": self.system_prompt,
            "model": self.model,
            "messages": [m.to_dict() for m in self.messages],
            "id": self.id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            **self._group_fields(),
        }

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "model": self.model,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "message_count": len(self.messages),
            **self._group_fields(),
        }


@dataclass
class Settings:
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    default_model: str = "gpt-4o"
    embedding_model: str = "text-embedding-3-small"
    system_prompt: str = "You are a helpful assistant."
    temperature: float = 0.7
    max_tokens: int = 4096
    top_p: float = 1.0
    dark_mode: bool = False
    # "system" | "light" | "dark" (new in v3; dark_mode kept for old files)
    theme: str = "system"
    rag_enabled: bool = True
    rag_min_score: float = 0.3
    knowledge_folder: str = ""
    # Server mode: folders (one per line, e.g. \\fileserver\share) under which users and groups
    # may link a shared folder as their knowledge base. Empty = linking is off.
    knowledge_roots: str = ""
    memory_enabled: bool = True
    disable_thinking: bool = False
    # MCP (new in v3)
    mcp_enabled: bool = True
    mcp_auto_approve: bool = False
    mcp_max_tool_rounds: int = 8

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Settings:
        s = cls()
        if "theme" not in d and d.get("dark_mode"):
            d = {**d, "theme": "dark"}  # config.json from before themes existed
        for f in fields(cls):
            if f.name in d and d[f.name] is not None:
                default = getattr(s, f.name)
                value = d[f.name]
                try:
                    if isinstance(default, bool):
                        value = bool(value)
                    elif isinstance(default, int):
                        value = int(value)
                    elif isinstance(default, float):
                        value = float(value)
                    elif isinstance(default, str):
                        value = str(value)
                except (TypeError, ValueError):
                    continue
                setattr(s, f.name, value)
        return s

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
