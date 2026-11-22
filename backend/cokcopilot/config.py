"""Where data lives, and loading/saving settings and conversations.

On-disk formats are the same as the Rust/Python versions, so existing
config.json, conversations, memory, skills and knowledge index load unchanged.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from .models import Conversation, Settings

REPO_ROOT = Path(__file__).resolve().parents[2]


def _compute_data_dir() -> Path:
    env = os.environ.get("COKCOPILOT_DATA_DIR")
    if env:
        return Path(env)
    # Running from a source checkout: keep data in the repo's data/ folder.
    if not getattr(sys, "frozen", False) and (REPO_ROOT / "backend" / "pyproject.toml").exists():
        return REPO_ROOT / "data"
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home()))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "CokCopilot"


DATA_DIR = _compute_data_dir()
CONFIG_FILE = DATA_DIR / "config.json"
CONVERSATIONS_DIR = DATA_DIR / "conversations"
KNOWLEDGE_DIR = DATA_DIR / "knowledge"
KNOWLEDGE_INDEX_FILE = DATA_DIR / "knowledge_index.json"
MEMORY_FILE = DATA_DIR / "memory.json"
SKILLS_DIR = DATA_DIR / "skills"
MCP_FILE = DATA_DIR / "mcp.json"


def default_knowledge_folder() -> Path:
    if DATA_DIR == REPO_ROOT / "data":
        return DATA_DIR / "knowledge_files"
    docs = Path.home() / "Documents"
    return (docs if docs.is_dir() else Path.home()) / "CokCopilot Knowledge"


def knowledge_folder(settings: Settings) -> Path:
    return Path(settings.knowledge_folder) if settings.knowledge_folder else default_knowledge_folder()


def ensure_dirs() -> None:
    for d in (DATA_DIR, CONVERSATIONS_DIR, KNOWLEDGE_DIR, SKILLS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, value: Any) -> None:
    """Pretty JSON, 2-space indent, non-ASCII kept as-is; written atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def load_settings() -> Settings:
    ensure_dirs()
    return Settings.from_dict(read_json(CONFIG_FILE) or {})


def save_settings(settings: Settings) -> None:
    ensure_dirs()
    write_json(CONFIG_FILE, settings.to_dict())


def load_conversations(directory: Path | None = None) -> list[Conversation]:
    """Conversations in ``directory`` (default: the local user's), newest-modified first."""
    d = directory or CONVERSATIONS_DIR
    d.mkdir(parents=True, exist_ok=True)
    files = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for p in files:
        data = read_json(p)
        if not isinstance(data, dict):
            continue
        try:
            out.append(Conversation.from_dict(data))
        except (KeyError, TypeError, ValueError) as e:
            print(f"Skipping unreadable conversation {p}: {e}", file=sys.stderr)
    return out


def load_conversation(convo_id: str, directory: Path | None = None) -> Conversation | None:
    data = read_json((directory or CONVERSATIONS_DIR) / f"{_safe_id(convo_id)}.json")
    return Conversation.from_dict(data) if isinstance(data, dict) else None


def save_conversation(convo: Conversation, directory: Path | None = None) -> None:
    d = directory or CONVERSATIONS_DIR
    d.mkdir(parents=True, exist_ok=True)
    write_json(d / f"{_safe_id(convo.id)}.json", convo.to_dict())


def delete_conversation(convo_id: str, directory: Path | None = None) -> None:
    ((directory or CONVERSATIONS_DIR) / f"{_safe_id(convo_id)}.json").unlink(missing_ok=True)


def _safe_id(convo_id: str) -> str:
    if not convo_id or not all(c.isalnum() or c in "-_" for c in convo_id):
        raise ValueError(f"invalid conversation id: {convo_id!r}")
    return convo_id
