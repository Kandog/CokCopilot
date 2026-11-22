"""Ports of the Rust unit tests, so the Python backend keeps the same behaviour."""

import asyncio
import json
import zipfile
from pathlib import Path

import pytest

from cokcopilot import llm, rag
from cokcopilot.knowledge import Hit, KnowledgeBase, chunk_text, local_embed_one
from cokcopilot.memory import MemoryStore, parse_facts
from cokcopilot.models import Conversation, Message, Settings, new_id
from cokcopilot.parsers import extract_document_text, is_document, is_image
from cokcopilot.skills import SkillExists, SkillStore, parse_invocation, parse_skill_md, sanitize_name, skill_md


# -- models / settings --


def test_reads_old_conversation_format():
    data = json.loads("""{
        "title": "Hi", "system_prompt": "", "model": "",
        "messages": [{"role": "user", "content": "hello", "timestamp": "2026-05-01T10:20:30.123456",
                      "id": "abcdef123456", "images": [], "document_texts": []},
                     {"role": "assistant", "content": "hey", "timestamp": "2026-05-01T10:20:31", "id": "x"}],
        "id": "0887d7ba6bd8", "created_at": "2026-05-01T10:20:30.000001", "updated_at": "2026-05-01T10:20:31.5"}""")
    c = Conversation.from_dict(data)
    assert len(c.messages) == 2 and c.messages[1].role == "assistant"
    out = c.to_dict()
    assert "skill" not in out["messages"][0] and "tool_calls" not in out["messages"][0]
    assert Conversation.from_dict(out).messages[0].timestamp == c.messages[0].timestamp


def test_settings_fill_missing_fields():
    s = Settings.from_dict({"api_key": "k", "temperature": 1})
    assert s.api_key == "k" and s.temperature == 1.0 and s.default_model == "gpt-4o" and s.rag_enabled


def test_ids_are_twelve_hex_chars():
    i = new_id()
    assert len(i) == 12 and all(c in "0123456789abcdef" for c in i)


# -- think filter / messages --


def feed_all(chunks):
    f = llm.ThinkTagFilter()
    return "".join(f.feed(c) for c in chunks) + f.flush()


def test_think_filter():
    assert llm.strip_think_tags("<think>hmm</think>  Answer") == "Answer"
    assert feed_all(["Hi <th", "ink>secret</thi", "nk>\n there"]) == "Hi there"
    assert feed_all(["a <", "b"]) == "a <b"
    assert feed_all(["end <thi"]) == "end <thi"
    assert feed_all(["ok <think>never closed"]) == "ok "
    assert feed_all(["你好<think>想", "</think>世界"]) == "你好世界"


def test_builds_multipart_content_for_attachments():
    m = Message("user", "look", images=["AAAA"], document_texts=["doc"])
    msgs = llm.build_messages(Settings(), "", [m])
    assert msgs[0] == {"role": "system", "content": "You are a helpful assistant."}
    parts = msgs[1]["content"]
    assert len(parts) == 3
    assert parts[1]["image_url"]["url"] == "data:image/jpeg;base64,AAAA"
    assert parts[2]["text"] == "[Document content]\ndoc"


# -- memory --


def test_memory_dedups(tmp_path):
    m = MemoryStore(tmp_path / "memory.json")
    assert m.add("Prefers Rust over Python")
    assert m.add("prefers rust over python!") is None
    assert m.add("Prefers Rust") is None
    assert m.add("Uses Windows 11")
    assert len(MemoryStore(tmp_path / "memory.json").list()) == 2


def test_parse_facts():
    assert parse_facts('```json\n["a", "b", 3]\n```') == ["a", "b"]
    assert parse_facts("nothing here") == []
    assert parse_facts("[]") == []


# -- skills --


def test_parse_skill_md_and_invocations():
    name, desc, body = parse_skill_md('---\nname: "Summarize"\ndescription: Short bullets\nlicense: MIT\n---\n\nDo it.\n')
    assert (name, desc, body) == ("Summarize", "Short bullets", "Do it.")
    assert parse_skill_md("Just instructions") == (None, "", "Just instructions")
    assert parse_invocation("/sum  hello world") == ("sum", "hello world")
    assert parse_invocation("/sum") == ("sum", "")
    assert parse_invocation("hi /sum") is None
    assert parse_invocation("/ x") is None
    assert sanitize_name(" My Skill!! ") == "my-skill"


def test_installs_folders_files_and_zips(tmp_path):
    src = tmp_path / "src"
    store = SkillStore(tmp_path / "store")
    folder = src / "translator"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(skill_md("translate", "Translate text", "Translate to French."))
    (folder / "glossary.txt").write_text("cat = chat")
    assert store.install_path(folder) == "translate"
    assert "cat = chat" in store.get("Translate").prompt_block()
    with pytest.raises(SkillExists):
        store.install_path(folder)
    store.install_path(folder, overwrite=True)

    md = src / "Code Review.md"
    md.write_text("Review the code.")
    assert store.install_path(md) == "code-review"

    zp = src / "s.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("haiku/SKILL.md", skill_md("haiku", "Write a haiku", "Answer as a haiku."))
    assert store.install_path(zp) == "haiku"
    assert [s.name for s in store.skills] == ["code-review", "haiku", "translate"]
    assert not any(p.name.startswith(".") for p in store.dir.iterdir())
    store.create("my notes", "", "Be brief.")
    assert store.get("my-notes")


# -- rag --


def hit(path, text):
    return Hit(path, text, 1.0)


def run_filter(chunks):
    f = rag.SourcesFilter()
    shown = "".join(f.feed(c) for c in chunks)
    tail, cited = f.finish()
    return shown + tail, cited


def test_strict_prompt():
    p = rag.strict_system_prompt("Be brief.", [hit("a.md", "Alpha fact."), hit("b/c.pdf", "Beta fact.")])
    assert p.startswith("Be brief.") and "ONLY the information" in p and rag.DONT_KNOW in p
    assert "[Excerpt 1 — a.md]\nAlpha fact." in p and "[Excerpt 2 — b/c.pdf]\nBeta fact." in p


def test_sources_filter():
    assert run_filter(["The answer is 42.\n\nSOU", "RCES: 2", ", 3"]) == ("The answer is 42.\n\n", [2, 3])
    assert run_filter(["Done.\n**Sources:** [1]\n"]) == ("Done.\n", [1])
    assert run_filter(["So", "me text\nSource code is ", "fine.\nS"]) == ("Some text\nSource code is fine.\nS", None)


def test_citations_and_footer():
    hits = [hit("a.md", "x"), hit("b.md", "y"), hit("c.md", "z"), hit("a.md", "w")]
    assert rag.references_footer(rag.cited_hits(hits, "anything", [2, 9])) == "\n\n---\n**References**\n- b.md"
    hits = [hit("vacation.md", "Employees get 20 vacation days per year, accrued monthly."),
            hit("expenses.md", "Submit expense reports within 30 days of purchase."),
            hit("security.md", "Badges must be worn at all times in the office.")]
    answer = "You get 20 vacation days per year, accrued monthly."
    assert [h.rel_path for h in rag.cited_hits(hits, answer, None)] == ["vacation.md"]
    assert rag.cited_hits(hits, answer, [7])[0].rel_path == "vacation.md"
    assert rag.references_footer([hit("b.pdf", "1"), hit("a.md", "2"), hit("b.pdf", "3")]) == \
        "\n\n---\n**References**\n- b.pdf\n- a.md"
    assert rag.is_dont_know(rag.DONT_KNOW)
    assert rag.is_dont_know("  I don’t know. The excerpts don't say.")
    assert not rag.is_dont_know("The capybara is the largest rodent. I don't know its weight.")


# -- knowledge base --


def test_chunking_and_local_embedding():
    text = ("Sentence number one. " * 100).strip()
    chunks = chunk_text(text, 200, 50)
    assert len(chunks) > 5 and all(len(c) <= 200 for c in chunks)
    v = local_embed_one("hello world hello")
    assert len(v) == 2048 and abs(sum(x * x for x in v) - 1) < 1e-6


def test_kb_sync_and_retrieve_offline(tmp_path):
    folder = tmp_path / "kb"
    folder.mkdir()
    (folder / "animals.md").write_text("The capybara is the largest rodent.")
    kb = KnowledgeBase(folder, tmp_path / "index.json", tmp_path / "chunks")
    offline = Settings(base_url="http://127.0.0.1:9")

    async def go():
        r = await kb.sync(offline)
        assert r.added == ["animals.md"] and kb.embedding_method == "local-hash"
        hits = await kb.retrieve("How large is a capybara?", offline, 4, 0.3)
        assert hits and hits[0].rel_path == "animals.md"
        assert await kb.retrieve("What is the capital of France?", offline, 4, 0.3) == []
        (folder / "animals.md").unlink()
        r = await kb.sync(offline)
        assert r.removed == ["animals.md"]

    asyncio.run(go())
    # The index reloads from disk.
    assert KnowledgeBase(folder, tmp_path / "index.json", tmp_path / "chunks").files == []


def test_docx_and_classification(tmp_path):
    assert is_image("a/B.PNG") and is_document("notes.Md") and not is_document("archive.zip")
    p = tmp_path / "t.docx"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("word/document.xml", """<?xml version="1.0"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
<w:p><w:r><w:t>Hello</w:t></w:r><w:r><w:t xml:space="preserve"> &amp; bye</w:t></w:r></w:p>
<w:p><w:r><w:t>  </w:t></w:r></w:p>
<w:p><w:r><w:t>A</w:t><w:tab/><w:t>B</w:t></w:r></w:p>
</w:body></w:document>""")
    assert extract_document_text(p) == "Hello & bye\nA\tB"


def test_existing_repo_data_loads():
    """The repo's real data folder (from the Rust version) must load."""
    data = Path(__file__).resolve().parents[2] / "data"
    if not (data / "conversations").is_dir():
        pytest.skip("no repo data")
    for f in (data / "conversations").glob("*.json"):
        Conversation.from_dict(json.loads(f.read_text(encoding="utf-8")))
    Settings.from_dict(json.loads((data / "config.json").read_text(encoding="utf-8")))
