"""MCP manager against a real stdio server, plus the tool-calling chat loop with a fake model."""

import asyncio
import json
import sys
from pathlib import Path

import pytest

from cokcopilot.usage import UsageLog
from cokcopilot import chat, llm
from cokcopilot.auth import LOCAL_USER
from cokcopilot.state import UserCtx
from cokcopilot.mcp_manager import MCPConfigError, MCPManager, normalize_server_config
from cokcopilot.models import Conversation, Message, Settings

ECHO = str(Path(__file__).with_name("echo_server.py"))


def test_normalize_config():
    assert normalize_server_config({"command": "npx", "args": ["-y", "x"]})["type"] == "stdio"
    assert normalize_server_config({"url": "https://a/mcp"})["type"] == "http"
    assert normalize_server_config({"type": "streamable-http", "url": "http://a"})["type"] == "http"
    with pytest.raises(MCPConfigError):
        normalize_server_config({"type": "stdio"})
    with pytest.raises(MCPConfigError):
        normalize_server_config({"url": "ftp://x"})


def test_stdio_server_tools_and_calls(tmp_path, monkeypatch):
    monkeypatch.setattr("cokcopilot.config.DATA_DIR", tmp_path)

    async def go():
        mgr = MCPManager(tmp_path / "mcp.json")
        await mgr.import_json(json.dumps({"mcpServers": {"echo": {"command": sys.executable, "args": [ECHO]}}}))
        try:
            rt = mgr.get("echo")
            assert rt.status == "connected", rt.error
            assert {t.name for t in rt.tools} == {"add", "shout"}
            tools, mapping = mgr.openai_tools()
            assert mapping["echo__add"] == ("echo", "add")
            assert tools[0]["function"]["parameters"]["type"] == "object"
            assert "Use add" in mgr.instructions()
            text, is_err = await mgr.call_tool("echo", "add", {"a": 2, "b": 40})
            assert not is_err and text.strip() == "42"
            mgr.set_tool_flags("echo", "shout", enabled=False)
            assert [t["function"]["name"] for t in mgr.openai_tools()[0]] == ["echo__add"]
            saved = json.loads((tmp_path / "mcp.json").read_text())
            assert saved["mcpServers"]["echo"]["disabledTools"] == ["shout"]
            await mgr.set_enabled("echo", False)
            assert rt.status == "disabled" and mgr.openai_tools()[0] == []
        finally:
            await mgr.stop_all()

    asyncio.run(go())


def test_broken_server_reports_error(tmp_path, monkeypatch):
    monkeypatch.setattr("cokcopilot.config.DATA_DIR", tmp_path)

    async def go():
        mgr = MCPManager(tmp_path / "mcp.json")
        rt = await mgr.upsert("nope", {"command": "definitely-not-a-real-command-xyz"})
        assert rt.status == "error" and rt.error
        await mgr.stop_all()

    asyncio.run(go())


def test_reply_loop_runs_tools_with_approval(tmp_path, monkeypatch):
    """Fake model: first asks for echo__add, then answers with the tool result."""
    monkeypatch.setattr("cokcopilot.config.DATA_DIR", tmp_path)
    monkeypatch.setattr("cokcopilot.config.CONVERSATIONS_DIR", tmp_path / "conversations")
    seen_messages = []

    async def fake_stream(settings, model, messages, tools=None):
        seen_messages.append(list(messages))
        if tools and not any(m["role"] == "tool" for m in messages):
            yield {"type": "text", "text": "Let me add."}
            yield {"type": "end", "tool_calls": [{"id": "c1", "name": "echo__add", "arguments": '{"a": 1, "b": 2}'}]}
        else:
            result = next(m["content"] for m in messages if m["role"] == "tool")
            yield {"type": "text", "text": f"The sum is {result.strip()}."}
            yield {"type": "end", "tool_calls": []}

    monkeypatch.setattr(llm, "stream_completion", fake_stream)

    class FakeMemory:
        def as_system_prompt_block(self):
            return ""

    class FakeSkills:
        def get(self, name):
            return None

    async def go():
        mgr = MCPManager(tmp_path / "mcp.json")
        await mgr.upsert("echo", {"command": sys.executable, "args": [ECHO]})
        state = type("S", (), {})()
        state.settings = Settings(rag_enabled=False, memory_enabled=False)
        state.mcp, state.skills = mgr, FakeSkills()
        state.usage = UsageLog(tmp_path / "usage")
        ctx = UserCtx(LOCAL_USER, state.settings, FakeMemory(), tmp_path / "conversations", None)
        convo = Conversation(messages=[Message("user", "what is 1+2?")])
        stream = chat.Stream("s1", convo.id)
        task = asyncio.create_task(chat.run_reply(state, ctx, stream, convo))
        events = []
        while True:
            ev = await asyncio.wait_for(stream.queue.get(), 30)
            if ev is chat._DONE:
                break
            events.append(ev)
            if ev["type"] == "tool_call" and ev["call"]["status"] == "awaiting_approval":
                stream.approvals[ev["call"]["id"]].set_result({"approved": True})
        await task
        await mgr.stop_all()
        return events

    events = asyncio.run(go())
    statuses = [e["call"]["status"] for e in events if e["type"] == "tool_call"]
    assert statuses == ["awaiting_approval", "running", "done"]
    done = next(e for e in events if e["type"] == "done")
    assert done["message"]["content"] == "Let me add.\n\nThe sum is 3."
    assert done["message"]["tool_calls"][0]["result"].strip() == "3"
    assert seen_messages[1][-2]["tool_calls"][0]["function"]["name"] == "echo__add"


def test_reply_records_usage(tmp_path, monkeypatch):
    """Reported token counts are summed over tool rounds; missing ones are estimated."""
    monkeypatch.setattr("cokcopilot.config.DATA_DIR", tmp_path)

    async def fake_stream(settings, model, messages, tools=None):
        yield {"type": "text", "text": "Hello there"}
        yield {"type": "end", "tool_calls": [], "usage": {"prompt_tokens": 100, "completion_tokens": 7}}

    monkeypatch.setattr(llm, "stream_completion", fake_stream)

    class FakeMemory:
        def as_system_prompt_block(self):
            return ""

    state = type("S", (), {})()
    state.settings = Settings(rag_enabled=False, memory_enabled=False, mcp_enabled=False)
    state.mcp, state.skills = MCPManager(tmp_path / "mcp.json"), type("K", (), {"get": lambda self, n: None})()
    state.usage = UsageLog(tmp_path / "usage")
    ctx = UserCtx(LOCAL_USER, state.settings, FakeMemory(), tmp_path / "conversations", None)
    convo = Conversation(messages=[Message("user", "hi")], model="m1")
    asyncio.run(chat.run_reply(state, ctx, chat.Stream("s1", convo.id), convo))
    summary = state.usage.summary(7)
    assert summary["totals"]["requests"] == 1
    assert summary["totals"]["total_tokens"] == 107 and not summary["totals"]["estimated"]
    assert summary["users"][0]["user_id"] == LOCAL_USER.id and summary["users"][0]["top_model"] == "m1"
    assert summary["models"][0]["model"] == "m1" and len(summary["daily"]) == 7
