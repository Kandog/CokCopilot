"""Producing one assistant reply: plan the prompt, stream, run MCP tools, save.

A reply runs as its own asyncio task that pushes events into a queue; the
HTTP response just relays the queue as Server-Sent Events. That way "Stop"
can cancel the task even while it waits on the network, and tool-call
approvals can be answered through a separate request.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from . import config, llm, memory, rag, skills
from .usage import KIND_MEMORY, Usage
from .knowledge import Hit
from .models import NEW_CONVERSATION_TITLE, Conversation, Message, ToolCallRecord, new_id, now_iso

if TYPE_CHECKING:
    from .state import AppState, UserCtx

MAX_UI_RESULT_CHARS = 4000
_DONE = object()


@dataclass
class ReplyPlan:
    system_prompt: str
    hits: list[Hit] = field(default_factory=list)
    fixed: str | None = None  # answer without asking the model


@dataclass
class Stream:
    id: str
    conversation_id: str
    user_id: str = ""
    group_id: str = ""
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    task: asyncio.Task | None = None
    approvals: dict[str, asyncio.Future] = field(default_factory=dict)

    def emit(self, event: dict[str, Any]) -> None:
        self.queue.put_nowait(event)


def active_skills(store: skills.SkillStore, messages: list[Message]) -> list[skills.Skill]:
    """Skills invoked anywhere in the conversation, oldest first, each once."""
    out: list[skills.Skill] = []
    for m in messages:
        if m.skill:
            s = store.get(m.skill)
            if s and all(o.name != s.name for o in out):
                out.append(s)
    return out


async def plan_reply(state: AppState, ctx: UserCtx, convo: Conversation, use_tools: bool) -> ReplyPlan:
    settings = ctx.settings
    parts = [convo.system_prompt or settings.system_prompt]
    # Memory holds private facts about the user; group chats are read by every member.
    if settings.memory_enabled and not ctx.group:
        block = ctx.memory.as_system_prompt_block()
        if settings.rag_enabled and block:
            # Memory may shape tone and format, but must not become a source of facts.
            block += "\n(Use these only for tone and formatting preferences, not as facts.)"
        parts.append(block)
    parts.extend(s.prompt_block() for s in active_skills(state.skills, convo.messages))
    if use_tools:
        parts.append(state.mcp.instructions())
    preamble = "\n\n".join(p for p in parts if p)

    if not settings.rag_enabled:
        return ReplyPlan(preamble)

    question = ""
    for m in reversed(convo.messages):
        if m.role == "user":
            inv = skills.parse_invocation(m.content)
            question = inv[1] if (inv and m.skill) else m.content  # search with the skill's input
            break
    hits, error = await search_knowledge(state, ctx, question)
    if error and not hits:
        return ReplyPlan(preamble, fixed=(
            f"I can't answer right now: searching your knowledge base failed ({error}). "
            "Check the embedding model in Settings, or try again."))
    if not hits:
        return ReplyPlan(preamble, fixed=rag.DONT_KNOW)
    return ReplyPlan(rag.strict_system_prompt(preamble, hits), hits)


# In server mode a reply searches the user's own documents and the team's shared ones.
# In a group chat it searches the group's documents and the team's instead: every member can
# read the chat, so the user's private documents must not leak into it.
MY_DOCS = "My documents"
TEAM_DOCS = "Team documents"


async def search_knowledge(state: AppState, ctx: UserCtx, question: str) -> tuple[list[Hit], str]:
    """Best excerpts across the knowledge bases this user can read, and the first error (if any).

    Each knowledge base filters for relevance with its own embedding method; when there
    are two, their names prefix the document paths so the References say where each came from.
    """
    settings = ctx.settings
    if ctx.group and ctx.group_kb:
        sources = [(ctx.group_kb, ctx.group.name), (state.kb, TEAM_DOCS)]
    elif ctx.kb:
        sources = [(ctx.kb, MY_DOCS), (state.kb, TEAM_DOCS)]
    else:
        sources = [(state.kb, "")]
    hits: list[Hit] = []
    error = ""
    for kb, label in sources:
        if not kb.watched:  # a watched (linked) folder is kept in sync in the background
            await kb.sync(settings)
        try:
            found = await kb.retrieve(question, settings, rag.TOP_K, settings.rag_min_score)
        except llm.LLMError as e:
            error = error or str(e)
            continue
        for h in found:
            if label:
                h.rel_path = f"{label}/{h.rel_path}"
        hits.extend(found)
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[: rag.TOP_K + (2 if len(sources) > 1 else 0)], error


def make_title(text: str) -> str:
    text = " ".join(text.split())
    return text[:50] + "..." if len(text) > 50 else text


def prepare_conversation(state: AppState, ctx: UserCtx, req: dict[str, Any]) -> tuple[Conversation, Message | None]:
    """Apply a chat request to its conversation (new message / edit / regenerate) and save it."""
    convo_id = req.get("conversation_id")
    convo = config.load_conversation(convo_id, ctx.convos_dir) if convo_id else None
    if convo is None:
        convo = Conversation()
        if ctx.group:
            convo.group_id, convo.user_id, convo.user_name = ctx.group.id, ctx.user.id, ctx.user.name
        if req.get("model"):
            convo.model = req["model"]
    elif req.get("model") is not None:
        convo.model = req["model"]

    user_msg = None
    if req.get("regenerate"):
        while convo.messages and convo.messages[-1].role == "assistant":
            convo.messages.pop()
        if not convo.messages:
            raise ValueError("nothing to regenerate")
    else:
        if req.get("edit_message_id"):
            idx = next((i for i, m in enumerate(convo.messages) if m.id == req["edit_message_id"]), None)
            if idx is None:
                raise ValueError("message to edit not found")
            del convo.messages[idx:]
        text = (req.get("content") or "").strip()
        attachments = req.get("attachments") or []
        if not text and not attachments:
            raise ValueError("empty message")
        skill_name = ""
        inv = skills.parse_invocation(text)
        if inv:
            if not state.skills.get(inv[0]):
                state.skills.reload()
            s = state.skills.get(inv[0])
            skill_name = s.name if s else ""
        user_msg = Message("user", text, skill=skill_name)
        for a in attachments:
            data = a.get("data") or ""
            if a.get("kind") == "image" and data:
                # Keep the whole data URL so the image keeps its real type (PNG, WebP, ...).
                if not (data.startswith("data:image/") and ";base64," in data[:100]):
                    raise ValueError(f"{a.get('name') or 'image'}: not an image")
                user_msg.images.append(data)
            elif a.get("text"):
                user_msg.document_texts.append(a["text"])
            else:
                continue
            user_msg.attachments.append(a.get("name") or "file")
        convo.messages.append(user_msg)
        if len(convo.messages) == 1 and convo.title == NEW_CONVERSATION_TITLE:
            convo.title = make_title(text or (user_msg.attachments[0] if user_msg.attachments else "New chat"))
    convo.updated_at = now_iso()
    config.save_conversation(convo, ctx.convos_dir)
    return convo, user_msg


async def run_reply(state: AppState, ctx: UserCtx, stream: Stream, convo: Conversation) -> None:
    """Producer task: emits events, saves the assistant message, then extracts memory."""
    settings = ctx.settings
    model = convo.model or settings.default_model
    answer: list[str] = []
    records: list[ToolCallRecord] = []
    error = ""
    usage = Usage()

    def text(t: str) -> None:
        if t:
            answer.append(t)
            stream.emit({"type": "text", "text": t})

    try:
        tools, mapping = state.mcp.openai_tools() if settings.mcp_enabled else ([], {})
        use_tools = bool(tools) and not settings.rag_enabled
        plan = await plan_reply(state, ctx, convo, use_tools)
        if plan.fixed is not None:
            text(plan.fixed)
        else:
            if tools and settings.rag_enabled:
                stream.emit({"type": "notice", "text": "Knowledge-only mode is on, so MCP tools are not used."})
            api_messages = llm.build_messages(settings, plan.system_prompt, convo.messages)
            sources = rag.SourcesFilter() if plan.hits else None
            max_rounds = max(1, settings.mcp_max_tool_rounds) if use_tools else 1
            for round_no in range(max_rounds + 1):
                offer = tools if (use_tools and round_no < max_rounds) else None
                if answer and not answer[-1].endswith("\n"):
                    text("\n\n")
                calls: list[dict[str, str]] = []
                round_text: list[str] = []
                reported: dict[str, Any] | None = None
                started = False
                try:
                    async for ev in llm.stream_completion(settings, model, api_messages, offer):
                        started = True
                        if ev["type"] == "text":
                            t = sources.feed(ev["text"]) if sources else ev["text"]
                            round_text.append(t)
                            text(t)
                        elif ev["type"] == "notice":
                            stream.emit(ev)
                        elif ev["type"] == "end":
                            calls = ev["tool_calls"]
                            reported = ev.get("usage")
                finally:
                    # Count this round even if it was stopped part-way (the provider still bills it).
                    if started:
                        usage.add(reported, json.dumps(api_messages, ensure_ascii=False),
                                  "".join(round_text) + "".join(c["arguments"] for c in calls))
                if sources:
                    tail, cited = sources.finish()
                    text(tail)
                    full = "".join(answer)
                    if full.strip() and not rag.is_dont_know(full):
                        text(rag.references_footer(rag.cited_hits(plan.hits, full, cited)))
                if not calls:
                    break
                for c in calls:
                    c["id"] = c["id"] or f"call_{new_id()}"
                api_messages.append({
                    "role": "assistant",
                    "content": "".join(round_text) or None,
                    "tool_calls": [{"id": c["id"], "type": "function",
                                    "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}}
                                   for c in calls],
                })
                for c in calls:
                    result = await _run_tool(state, ctx, stream, c, mapping, records)
                    api_messages.append({"role": "tool", "tool_call_id": c["id"], "content": result})
    except asyncio.CancelledError:
        stream.emit({"type": "notice", "text": "Stopped."})
    except llm.LLMError as e:
        error = str(e)
    except Exception as e:  # noqa: BLE001 - report anything to the UI rather than hang
        error = f"{type(e).__name__}: {e}"
    finally:
        for fut in stream.approvals.values():
            if not fut.done():
                fut.cancel()
        for rec in records:
            if rec.status in ("pending", "awaiting_approval", "running"):
                rec.status, rec.result = "error", rec.result or "Stopped before the tool finished."
        content = "".join(answer).strip()
        saved = None
        if content or records:
            saved = Message("assistant", content, tool_calls=records)
            fresh = config.load_conversation(convo.id, ctx.convos_dir) or convo
            fresh.messages.append(saved)
            fresh.updated_at = now_iso()
            config.save_conversation(fresh, ctx.convos_dir)
            convo = fresh
        state.usage.record(ctx.user.id, ctx.user.name, model, usage, group_id=ctx.group.id if ctx.group else "",
                           tool_calls=len(records), error=bool(error))
        if error:
            stream.emit({"type": "error", "error": error})
        stream.emit({"type": "done", "message": saved.to_dict() if saved else None,
                     "conversation": convo.summary()})
    if saved and not error:
        await _learn(state, ctx, stream, convo, model, content)
    stream.queue.put_nowait(_DONE)


async def _run_tool(state: AppState, ctx: UserCtx, stream: Stream, call: dict[str, str],
                    mapping: dict[str, tuple[str, str]], records: list[ToolCallRecord]) -> str:
    server, tool = mapping.get(call["name"], ("", call["name"]))
    try:
        args = json.loads(call["arguments"] or "{}")
        if not isinstance(args, dict):
            raise ValueError("arguments must be a JSON object")
    except ValueError as e:
        args = {}
        rec = ToolCallRecord(call["id"], server, tool, {}, f"Invalid arguments from the model: {e}", "error")
        records.append(rec)
        stream.emit({"type": "tool_call", "call": asdict(rec)})
        return rec.result
    rec = ToolCallRecord(call["id"], server, tool, args, "", "pending")
    records.append(rec)
    if not server:
        rec.status, rec.result = "error", f"Unknown tool {call['name']}"
        stream.emit({"type": "tool_call", "call": asdict(rec)})
        return rec.result

    needs_ok = not (state.settings.mcp_auto_approve or state.mcp.is_auto_approved(server, tool))
    if needs_ok:
        rec.status = "awaiting_approval"
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        stream.approvals[rec.id] = fut
        stream.emit({"type": "tool_call", "call": asdict(rec)})
        try:
            decision = await fut
        finally:
            stream.approvals.pop(rec.id, None)
        if decision.get("always") and ctx.user.is_admin:
            state.mcp.set_tool_flags(server, tool, auto_approve=True)
        if not decision.get("approved"):
            rec.status, rec.result = "denied", "The user declined this tool call."
            stream.emit({"type": "tool_call", "call": asdict(rec)})
            return rec.result + " Do not retry it; continue without it."

    rec.status = "running"
    stream.emit({"type": "tool_call", "call": asdict(rec)})
    result, is_error = await state.mcp.call_tool(server, tool, args)
    rec.status = "error" if is_error else "done"
    rec.result = result[:MAX_UI_RESULT_CHARS] + ("…" if len(result) > MAX_UI_RESULT_CHARS else "")
    stream.emit({"type": "tool_call", "call": asdict(rec)})
    return result


async def _learn(state: AppState, ctx: UserCtx, stream: Stream, convo: Conversation, model: str, answer: str) -> None:
    if not ctx.settings.memory_enabled or ctx.group:
        return
    user_text = next((m.content for m in reversed(convo.messages) if m.role == "user"), "")
    used = Usage()
    try:
        facts = await memory.extract_facts(ctx.settings, model, user_text, answer, on_usage=used.add)
    except Exception:  # noqa: BLE001 - memory is best effort
        return
    finally:
        state.usage.record(ctx.user.id, ctx.user.name, model, used, kind=KIND_MEMORY)
    added = [e.text for f in facts if (e := ctx.memory.add(f))]
    if added:
        stream.emit({"type": "memory", "added": added})


async def relay(stream: Stream):
    """Async iterator of SSE lines for the HTTP response."""
    try:
        while True:
            ev = await stream.queue.get()
            if ev is _DONE:
                break
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
    finally:
        # Client went away (tab closed / aborted): stop generating.
        if stream.task and not stream.task.done():
            stream.task.cancel()
