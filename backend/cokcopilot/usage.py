"""Usage log: one line per model call, for the admins' usage dashboard.

Each reply (all of its tool rounds together) and each background call (memory
extraction) appends a JSON line to ``usage/<YYYY-MM>.jsonl`` in the data dir:

    {"ts": "...", "user_id": "...", "user_name": "...", "group_id": "", "kind": "chat",
     "model": "gpt-4o", "prompt_tokens": 812, "completion_tokens": 240, "estimated": false,
     "tool_calls": 0, "error": false}

Token counts come from the provider (``usage`` in the response). Providers that
don't report usage get an estimate (about 4 characters per token), flagged
``estimated`` so the dashboard can say so. Only counts are stored, never message text.
"""

from __future__ import annotations

import json
import threading
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from . import config

KIND_CHAT = "chat"
KIND_MEMORY = "memory"
MAX_DAYS = 366


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4) if text else 0


@dataclass
class Usage:
    """Tokens used by one reply, summed over its rounds."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated: bool = False
    calls: int = 0

    def add(self, reported: dict[str, Any] | None, prompt_text: str, completion_text: str) -> None:
        self.calls += 1
        p = _int((reported or {}).get("prompt_tokens"))
        c = _int((reported or {}).get("completion_tokens"))
        if p is None or c is None:
            self.estimated = True
            p = estimate_tokens(prompt_text) if p is None else p
            c = estimate_tokens(completion_text) if c is None else c
        self.prompt_tokens += p
        self.completion_tokens += c


def _int(v: Any) -> int | None:
    try:
        return max(0, int(v)) if v is not None else None
    except (TypeError, ValueError):
        return None


@dataclass
class Entry:
    ts: str
    user_id: str
    user_name: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    kind: str = KIND_CHAT
    group_id: str = ""
    estimated: bool = False
    tool_calls: int = 0
    error: bool = False


@dataclass
class _Agg:
    requests: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    tool_calls: int = 0
    errors: int = 0
    estimated: bool = False
    last_active: str = ""
    models: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def add(self, e: Entry) -> None:
        if e.kind == KIND_CHAT:
            self.requests += 1
            self.models[e.model] += 1
        self.prompt_tokens += e.prompt_tokens
        self.completion_tokens += e.completion_tokens
        self.tool_calls += e.tool_calls
        self.errors += int(e.error)
        self.estimated |= e.estimated
        self.last_active = max(self.last_active, e.ts)

    def out(self) -> dict[str, Any]:
        top = max(self.models.items(), key=lambda kv: kv[1])[0] if self.models else ""
        return {"requests": self.requests, "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "total_tokens": self.prompt_tokens + self.completion_tokens, "tool_calls": self.tool_calls,
                "errors": self.errors, "estimated": self.estimated, "last_active": self.last_active,
                "top_model": top}


class UsageLog:
    def __init__(self, directory: Path | None = None):
        self.dir = directory or config.DATA_DIR / "usage"
        self._lock = threading.Lock()

    def record(self, user_id: str, user_name: str, model: str, usage: Usage, kind: str = KIND_CHAT,
               group_id: str = "", tool_calls: int = 0, error: bool = False) -> None:
        if not usage.calls:
            return
        e = Entry(datetime.now().isoformat(timespec="seconds"), user_id, user_name, model or "",
                  usage.prompt_tokens, usage.completion_tokens, kind, group_id, usage.estimated, tool_calls, error)
        line = json.dumps(asdict(e), ensure_ascii=False) + "\n"
        try:
            with self._lock:
                self.dir.mkdir(parents=True, exist_ok=True)
                with (self.dir / f"{e.ts[:7]}.jsonl").open("a", encoding="utf-8") as f:
                    f.write(line)
        except OSError as err:  # usage is bookkeeping: never fail a reply over it
            print(f"Couldn't record usage: {err}")

    def entries(self, since: date, until: date) -> list[Entry]:
        out: list[Entry] = []
        month = date(since.year, since.month, 1)
        lo, hi = since.isoformat(), (until + timedelta(days=1)).isoformat()
        while month <= until:
            path = self.dir / f"{month:%Y-%m}.jsonl"
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                lines = []
            for line in lines:
                try:
                    d = json.loads(line)
                    e = Entry(**{k: d[k] for k in Entry.__dataclass_fields__ if k in d})
                except (ValueError, TypeError, KeyError):
                    continue
                if lo <= e.ts < hi:
                    out.append(e)
            month = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
        return out

    def summary(self, days: int = 30, user_id: str | None = None, names: dict[str, str] | None = None,
                today: date | None = None) -> dict[str, Any]:
        """Totals, per user, per model and per day over the last ``days`` days (today included)."""
        days = max(1, min(int(days), MAX_DAYS))
        until = today or date.today()
        since = until - timedelta(days=days - 1)
        names = names or {}
        everything = self.entries(since, until)
        picked = [e for e in everything if not user_id or e.user_id == user_id]

        total, per_user, per_model = _Agg(), defaultdict(_Agg), defaultdict(_Agg)
        per_day: dict[str, _Agg] = {(since + timedelta(days=i)).isoformat(): _Agg() for i in range(days)}
        last_name: dict[str, str] = {}
        for e in everything:
            per_user[e.user_id].add(e)
            last_name[e.user_id] = e.user_name
        for e in picked:
            total.add(e)
            per_model[e.model or "(default)"].add(e)
            if e.ts[:10] in per_day:
                per_day[e.ts[:10]].add(e)

        users = []
        for uid, agg in per_user.items():
            users.append({"user_id": uid, "name": names.get(uid) or last_name.get(uid) or uid,
                          "removed": bool(names) and uid not in names, **agg.out()})
        users.sort(key=lambda u: (u["total_tokens"], u["requests"]), reverse=True)
        return {
            "days": days, "since": since.isoformat(), "until": until.isoformat(), "user_id": user_id or "",
            "totals": {**total.out(), "active_users": len({e.user_id for e in picked})},
            "users": users,
            "models": sorted(({"model": m, **a.out()} for m, a in per_model.items()),
                             key=lambda x: x["total_tokens"], reverse=True),
            "daily": [{"date": d, "requests": a.requests, "total_tokens": a.prompt_tokens + a.completion_tokens}
                      for d, a in per_day.items()],
        }
