"""Long-term AI memory: short facts about the user, learned from conversations."""

from __future__ import annotations

import json
import re
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

from . import config, llm
from .models import Message, Settings, new_id, now_iso

MAX_MEMORY_ENTRIES = 200

EXTRACTION_SYSTEM_PROMPT = """You help maintain a long-term memory file about the user of a chat app.
Given one user message and the assistant's reply, decide if there is any new,
durable fact worth remembering for FUTURE, unrelated conversations — e.g. a
stated preference, their role/profession, a tool/language/framework they
consistently use, a standing instruction about how they want answers
formatted, a recurring project or goal. Do NOT record one-off question
details, secrets, or anything sensitive (passwords, health, financial or
otherwise private data).

Respond with ONLY a JSON array of short strings (max ~15 words each), one
per new fact. If there's nothing worth remembering, respond with exactly: []
Do not include any other text, explanation, or markdown formatting."""


@dataclass
class MemoryEntry:
    id: str
    text: str
    created_at: str = ""


def _normalize(text: str) -> str:
    return re.sub(r"\W+", " ", text.lower()).strip()


class MemoryStore:
    def __init__(self, path: Path | None = None):
        self.path = path or config.MEMORY_FILE
        self._lock = threading.Lock()
        data = config.read_json(self.path) or {}
        self.entries = [
            MemoryEntry(e["id"], e["text"], e.get("created_at", ""))
            for e in data.get("entries", [])
            if isinstance(e, dict) and "id" in e and "text" in e
        ]

    def _save(self) -> None:
        config.write_json(self.path, {"entries": [asdict(e) for e in self.entries]})

    def list(self) -> list[MemoryEntry]:
        with self._lock:
            return list(self.entries)

    def add(self, text: str) -> MemoryEntry | None:
        text = text.strip()
        if not text:
            return None
        norm = _normalize(text)
        with self._lock:
            for e in self.entries:
                existing = _normalize(e.text)
                if norm == existing or norm in existing or existing in norm:
                    return None
            entry = MemoryEntry(new_id(), text, now_iso())
            self.entries.append(entry)
            if len(self.entries) > MAX_MEMORY_ENTRIES:
                self.entries = self.entries[-MAX_MEMORY_ENTRIES:]
            self._save()
            return entry

    def remove(self, entry_id: str) -> None:
        with self._lock:
            self.entries = [e for e in self.entries if e.id != entry_id]
            self._save()

    def clear(self) -> None:
        with self._lock:
            self.entries = []
            self._save()

    def as_system_prompt_block(self) -> str:
        with self._lock:
            if not self.entries:
                return ""
            lines = "\n".join(f"- {e.text}" for e in self.entries)
        return f"What you remember about this user from past conversations:\n{lines}"


def parse_facts(raw: str) -> list[str]:
    cleaned = re.sub(r"(?m)^```(json)?|```$", "", raw.strip()).strip()
    try:
        items = json.loads(cleaned)
    except ValueError:
        return []
    return [i for i in items if isinstance(i, str)] if isinstance(items, list) else []


async def extract_facts(settings: Settings, model: str, user_text: str, assistant_text: str,
                        on_usage=None) -> list[str]:
    if not user_text.strip() and not assistant_text.strip():
        return []
    snippet = [Message("user", user_text[:2000]), Message("assistant", assistant_text[:2000])]
    raw = await llm.chat_complete(
        settings, model, EXTRACTION_SYSTEM_PROMPT, snippet, temperature=0.0, max_tokens=300, timeout=20,
        on_usage=on_usage,
    )
    return parse_facts(raw) if raw else []
