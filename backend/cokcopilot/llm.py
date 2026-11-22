"""OpenAI-compatible API client (async, httpx) with SSE streaming and tool calls."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx

from .models import Message, Settings

# Request fields that ask reasoning models to skip their thinking phase. There's
# no single standard, so we send the common ones and retry without them if a
# strict provider (e.g. OpenAI) rejects unknown fields:
#   chat_template_kwargs: vLLM / SGLang / NIM;  enable_thinking: DashScope;  think: Ollama
NO_THINKING_FIELDS: dict[str, Any] = {
    "chat_template_kwargs": {"thinking": False, "enable_thinking": False},
    "enable_thinking": False,
    "think": False,
}


class LLMError(Exception):
    pass


# Base URLs that rejected ``stream_options`` (token usage in streams); not asked again this run.
_NO_STREAM_USAGE: set[str] = set()


def build_messages(settings: Settings, system_prompt: str, messages: list[Message]) -> list[dict[str, Any]]:
    """Messages array for the API. Attachments become multi-part content."""
    out: list[dict[str, Any]] = []
    prompt = system_prompt or settings.system_prompt
    if prompt:
        out.append({"role": "system", "content": prompt})
    for m in messages:
        if m.role == "system":
            continue
        if m.images or m.document_texts:
            parts: list[dict[str, Any]] = []
            if m.content:
                parts.append({"type": "text", "text": m.content})
            for img in m.images:
                parts.append({"type": "image_url", "image_url": {"url": _image_url(img)}})
            for doc in m.document_texts:
                parts.append({"type": "text", "text": f"[Document content]\n{doc}"})
            out.append({"role": m.role, "content": parts})
        else:
            out.append({"role": m.role, "content": m.content})
    return out


def _image_url(img: str) -> str:
    return img if img.startswith("data:") else f"data:image/jpeg;base64,{img}"


class ThinkTagFilter:
    """Removes ``<think>...</think>`` blocks from streamed text; tags may span chunks."""

    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self) -> None:
        self.buf = ""
        self.inside = False

    def feed(self, chunk: str) -> str:
        self.buf += chunk
        out = []
        while self.buf:
            tag = self.CLOSE if self.inside else self.OPEN
            idx = self.buf.find(tag)
            if idx >= 0:
                if not self.inside:
                    out.append(self.buf[:idx])
                self.buf = self.buf[idx + len(tag) :]
                self.inside = not self.inside
                if not self.inside:
                    self.buf = self.buf.lstrip()
                continue
            keep = 0
            for n in range(min(len(tag) - 1, len(self.buf)), 0, -1):
                if tag.startswith(self.buf[-n:]):
                    keep = n
                    break
            split = len(self.buf) - keep
            if not self.inside:
                out.append(self.buf[:split])
            self.buf = self.buf[split:]
            break
        return "".join(out)

    def flush(self) -> str:
        rest, self.buf = self.buf, ""
        return "" if self.inside else rest


def strip_think_tags(text: str) -> str:
    f = ThinkTagFilter()
    return f.feed(text) + f.flush()


def _endpoint(settings: Settings, path: str) -> str:
    return f"{settings.base_url.rstrip('/')}/{path}"


def _headers(settings: Settings) -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if settings.api_key:
        h["Authorization"] = f"Bearer {settings.api_key}"
    return h


def _describe(resp: httpx.Response, body: str) -> str:
    body = body.strip()[:500]
    msg = f"HTTP {resp.status_code} {resp.reason_phrase} for url '{resp.url}'"
    return f"{msg}\n{body}" if body else msg


def _payload(settings: Settings, model: str, messages: list[dict], stream: bool, **extra: Any) -> dict[str, Any]:
    p: dict[str, Any] = {
        "model": model or settings.default_model,
        "messages": messages,
        "stream": stream,
        "temperature": settings.temperature,
        "max_tokens": settings.max_tokens,
        "top_p": settings.top_p,
    }
    p.update(extra)
    if settings.disable_thinking:
        p.update(NO_THINKING_FIELDS)
    return p


def _without_thinking(p: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in p.items() if k not in NO_THINKING_FIELDS}


async def stream_completion(
    settings: Settings,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Stream one completion.

    Yields ``{"type": "text", "text": ...}`` for content, an optional
    ``{"type": "notice", "text": ...}``, and finally
    ``{"type": "end", "tool_calls": [...]}`` where each tool call is
    ``{"id", "name", "arguments"}`` (arguments is the raw JSON string).
    """
    extra: dict[str, Any] = {"tools": tools} if tools else {}
    attempts = [_payload(settings, model, messages, True, **extra)]
    if settings.disable_thinking:
        attempts.append(_without_thinking(attempts[0]))
    if tools:
        # Models without tool support: answer without tools rather than failing.
        attempts.append(_without_thinking(_payload(settings, model, messages, True)))
    # Ask for token counts (for the usage dashboard) unless this endpoint has refused that before.
    base = settings.base_url.rstrip("/")
    if base not in _NO_STREAM_USAGE:
        attempts = [{**a, "stream_options": {"include_usage": True}} for a in attempts]

    timeout = httpx.Timeout(connect=10, read=180, write=30, pool=10)
    async with httpx.AsyncClient(timeout=timeout) as client:
        last_error = ""
        i = 0
        while i < len(attempts):
            payload = attempts[i]
            i += 1
            async with client.stream(
                "POST", _endpoint(settings, "chat/completions"), headers=_headers(settings), json=payload
            ) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", errors="replace")
                    last_error = _describe(resp, body)
                    if (resp.status_code in (400, 422) and "stream_options" in payload
                            and ("stream_options" in body or "include_usage" in body)):
                        # A strict provider that doesn't know stream_options: retry the same request without it.
                        _NO_STREAM_USAGE.add(base)
                        attempts = [{k: v for k, v in a.items() if k != "stream_options"} for a in attempts]
                        i -= 1
                        continue
                    if resp.status_code in (400, 404, 422) and i < len(attempts):
                        continue
                    raise LLMError(last_error)
                if tools and "tools" not in payload:
                    yield {"type": "notice", "text": "This model doesn't accept tools, so MCP tools were skipped."}
                async for event in _read_stream(resp, settings.disable_thinking):
                    yield event
                return
        raise LLMError(last_error)


async def _read_stream(resp: httpx.Response, filter_think: bool) -> AsyncIterator[dict[str, Any]]:
    think = ThinkTagFilter() if filter_think else None
    calls: dict[int, dict[str, str]] = {}
    usage: dict[str, Any] | None = None
    async for line in resp.aiter_lines():
        if not line.startswith("data:"):
            continue
        data_str = line[5:].strip()
        if data_str == "[DONE]":
            break
        try:
            data = json.loads(data_str)
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("error"):
            err = data["error"]
            raise LLMError(err.get("message") if isinstance(err, dict) else str(err))
        if isinstance(data.get("usage"), dict):
            usage = data["usage"]  # the last chunk (OpenAI), or every chunk (some providers): keep the latest
        choices = data.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta") or {}
        content = delta.get("content")
        if content:
            text = think.feed(content) if think else content
            if text:
                yield {"type": "text", "text": text}
        for tc in delta.get("tool_calls") or []:
            slot = calls.setdefault(tc.get("index", 0), {"id": "", "name": "", "arguments": ""})
            if tc.get("id"):
                slot["id"] = tc["id"]
            fn = tc.get("function") or {}
            if fn.get("name"):
                slot["name"] += fn["name"]
            if fn.get("arguments"):
                slot["arguments"] += fn["arguments"]
    if think:
        tail = think.flush()
        if tail:
            yield {"type": "text", "text": tail}
    yield {"type": "end", "tool_calls": [calls[k] for k in sorted(calls) if calls[k]["name"]], "usage": usage}


async def fetch_models(settings: Settings) -> list[str]:
    """Model ids from /models; raises LLMError with a readable reason on failure."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(_endpoint(settings, "models"), headers=_headers(settings))
    except httpx.HTTPError as e:
        raise LLMError(str(e) or type(e).__name__) from e
    if resp.status_code >= 400:
        raise LLMError(_describe(resp, resp.text))
    try:
        items = resp.json().get("data") or []
    except ValueError as e:
        raise LLMError("the /models endpoint didn't return JSON") from e
    return sorted({m["id"] for m in items if isinstance(m, dict) and m.get("id")})


async def embed_texts(settings: Settings, texts: list[str]) -> list[list[float]] | None:
    """Embeddings from /embeddings, or None if unavailable (callers fall back)."""
    if not texts:
        return []
    payload = {"model": settings.embedding_model or "text-embedding-3-small", "input": texts}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(_endpoint(settings, "embeddings"), headers=_headers(settings), json=payload)
        if resp.status_code >= 400:
            return None
        items = sorted(resp.json()["data"], key=lambda d: d.get("index", 0))
        vectors = [[float(x) for x in item["embedding"]] for item in items]
        return vectors if len(vectors) == len(texts) else None
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return None


async def chat_complete(
    settings: Settings,
    model: str,
    system_prompt: str,
    messages: list[Message],
    temperature: float,
    max_tokens: int,
    timeout: float,
    on_usage: Callable[[dict[str, Any] | None, str, str], None] | None = None,
) -> str | None:
    """Non-streaming completion for small background tasks. None on failure.

    ``on_usage(reported_usage, prompt_text, completion_text)`` is called after a successful call.
    """
    base = _payload(settings, model, build_messages(settings, system_prompt, messages), False)
    base.update(temperature=temperature, max_tokens=max_tokens)
    base.pop("top_p", None)
    attempts = [base, _without_thinking(base)] if settings.disable_thinking else [base]
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            for payload in attempts:
                resp = await client.post(
                    _endpoint(settings, "chat/completions"), headers=_headers(settings), json=payload
                )
                if resp.status_code in (400, 422) and payload is not attempts[-1]:
                    continue
                if resp.status_code >= 400:
                    return None
                body = resp.json()
                content = body["choices"][0]["message"]["content"] or ""
                if on_usage:
                    on_usage(body.get("usage") if isinstance(body.get("usage"), dict) else None,
                             json.dumps(payload["messages"], ensure_ascii=False), content)
                return strip_think_tags(content).strip() if settings.disable_thinking else content
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
        return None
    return None
