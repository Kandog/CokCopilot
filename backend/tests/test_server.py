"""Server mode: access keys, sessions, per-user isolation and admin-only actions."""

import pytest
from fastapi.testclient import TestClient

from cokcopilot import config
from cokcopilot.auth import UserStore


@pytest.fixture
def data(tmp_path, monkeypatch):
    for name, value in {
        "DATA_DIR": tmp_path,
        "CONFIG_FILE": tmp_path / "config.json",
        "CONVERSATIONS_DIR": tmp_path / "conversations",
        "KNOWLEDGE_DIR": tmp_path / "knowledge",
        "KNOWLEDGE_INDEX_FILE": tmp_path / "knowledge_index.json",
        "MEMORY_FILE": tmp_path / "memory.json",
        "SKILLS_DIR": tmp_path / "skills",
        "MCP_FILE": tmp_path / "mcp.json",
    }.items():
        monkeypatch.setattr(config, name, value)
    monkeypatch.setattr(config, "default_knowledge_folder", lambda: tmp_path / "kb")
    config.write_json(tmp_path / "config.json", {"base_url": "http://127.0.0.1:9/v1", "api_key": "sk-secret",
                                                 "rag_enabled": False, "memory_enabled": False})
    return tmp_path


def client(mode: str, monkeypatch, base="https://chat.example.com"):
    if mode == "server":
        monkeypatch.setenv("COKCOPILOT_MODE", "server")
    else:
        monkeypatch.delenv("COKCOPILOT_MODE", raising=False)
    from cokcopilot.app import app

    return TestClient(app, base_url=base)


def test_server_requires_login_and_isolates_users(data, monkeypatch):
    store = UserStore()
    admin, admin_key = store.add("Ann", "admin")
    bob, bob_key = store.add("Bob")

    with client("server", monkeypatch) as c:
        assert c.get("/api/health").json()["mode"] == "server"
        assert c.get("/api/conversations").status_code == 401
        assert c.post("/api/auth/login", json={"key": "cok_wrong"}).status_code == 401

        # Bearer key works for API clients.
        h_bob = {"Authorization": f"Bearer {bob_key}"}
        assert c.get("/api/me", headers=h_bob).json()["name"] == "Bob"

        # Cookie session for the web UI.
        r = c.post("/api/auth/login", json={"key": admin_key})
        assert r.status_code == 200 and r.json()["is_admin"]
        assert "secure" in r.headers["set-cookie"].lower() and "httponly" in r.headers["set-cookie"].lower()
        me = c.get("/api/me").json()
        assert me["name"] == "Ann" and me["mode"] == "server"

        # The LLM key never reaches the browser.
        s = c.get("/api/settings").json()
        assert s["api_key"] == "" and s["api_key_set"] is True

        # Preferences are per user; server settings need an admin.
        assert c.put("/api/settings", json={"theme": "dark"}, headers=h_bob).status_code == 200
        assert c.get("/api/settings", headers=h_bob).json()["theme"] == "dark"
        assert c.get("/api/settings").json()["theme"] == "system"
        assert c.put("/api/settings", json={"base_url": "http://evil"}, headers=h_bob).status_code == 403
        assert c.put("/api/settings", json={"temperature": 0.2, "api_key": ""}).status_code == 200
        assert config.read_json(data / "config.json")["api_key"] == "sk-secret"  # empty key = keep

        # Memory and conversations are per user.
        c.post("/api/memory", json={"text": "Bob likes Go"}, headers=h_bob)
        assert [m["text"] for m in c.get("/api/memory", headers=h_bob).json()] == ["Bob likes Go"]
        assert c.get("/api/memory").json() == []
        assert (data / "users" / bob.id / "memory.json").exists()

        # Admin-only actions.
        assert c.post("/api/skills", json={"name": "x", "instructions": "y"}, headers=h_bob).status_code == 403
        assert c.put("/api/mcp/servers/x", json={"config": {"command": "calc"}}, headers=h_bob).status_code == 403
        assert c.get("/api/users", headers=h_bob).status_code == 403
        assert c.post("/api/knowledge/open-folder").status_code == 400  # never on a server

        # User management from the UI.
        r = c.post("/api/users", json={"name": "Cy"}).json()
        assert r["key"].startswith("cok_")
        assert c.get("/api/me", headers={"Authorization": f"Bearer {r['key']}"}).json()["name"] == "Cy"
        c.patch(f"/api/users/{r['user']['id']}", json={"disabled": True})
        assert c.get("/api/me", headers={"Authorization": f"Bearer {r['key']}"}).status_code == 401
        assert c.patch(f"/api/users/{admin.id}", json={"role": "user"}).status_code == 400  # last admin

        # Resetting a key ends existing sessions.
        c.post(f"/api/users/{admin.id}/reset-key")
        assert c.get("/api/me").status_code == 401

        # Cross-site requests are refused.
        r = c.post("/api/memory", json={"text": "x"}, headers={**h_bob, "Origin": "https://evil.example"})
        assert r.status_code == 403


def test_conversations_are_private(data, monkeypatch):
    store = UserStore()
    _, a_key = store.add("A", "admin")
    _, b_key = store.add("B")
    from cokcopilot.models import Conversation

    with client("server", monkeypatch) as c:
        ha, hb = ({"Authorization": f"Bearer {k}"} for k in (a_key, b_key))
        a_user = c.get("/api/me", headers=ha).json()
        convo = Conversation(title="secret plans")
        config.save_conversation(convo, data / "users" / a_user["id"] / "conversations")
        assert [x["title"] for x in c.get("/api/conversations", headers=ha).json()] == ["secret plans"]
        assert c.get("/api/conversations", headers=hb).json() == []
        assert c.get(f"/api/conversations/{convo.id}", headers=hb).status_code == 404


def test_local_mode_has_no_login_but_only_localhost(data, monkeypatch):
    with client("local", monkeypatch, base="http://127.0.0.1:8765") as c:
        me = c.get("/api/me").json()
        assert me["is_admin"] and me["mode"] == "local"
        assert c.get("/api/settings").json()["api_key"] == ""
    with client("local", monkeypatch, base="http://192.168.1.5:8765") as c:
        assert c.get("/api/me").status_code == 403


def test_behind_iis_proxy_allowed_origin_and_secure_cookie(data, monkeypatch):
    """IIS forwards to http://127.0.0.1:<port>; the browser's Origin is the public HTTPS site."""
    _, key = UserStore().add("Ann", "admin")
    monkeypatch.setenv("COKCOPILOT_ALLOWED_ORIGINS", "https://chat.example.com")
    with client("server", monkeypatch, base="http://127.0.0.1:8765") as c:
        public = {"Origin": "https://chat.example.com"}
        r = c.post("/api/auth/login", json={"key": key}, headers=public)
        assert r.status_code == 200
        assert "secure" in r.headers["set-cookie"].lower()  # page is HTTPS even though the hop isn't
        # (The test client won't resend a Secure cookie over plain HTTP; browsers talk HTTPS to IIS.)
        auth = {"Authorization": f"Bearer {key}"}
        assert c.post("/api/memory", json={"text": "hi"}, headers={**public, **auth}).status_code == 200
        evil = {"Origin": "https://evil.example", **auth}
        assert c.post("/api/memory", json={"text": "x"}, headers=evil).status_code == 403


def test_each_user_has_own_knowledge_base(data, monkeypatch):
    from cokcopilot import chat

    store = UserStore()
    _, ann_key = store.add("Ann", "admin")
    _, bob_key = store.add("Bob")
    _, cy_key = store.add("Cy")
    ha, hb, hc = ({"Authorization": f"Bearer {k}"} for k in (ann_key, bob_key, cy_key))

    def names(r):
        return [f["rel_path"] for f in r.json()["files"]]

    with client("server", monkeypatch) as c:
        mine = {"files": ("bob.md", b"Bob's project codename is Bluebird.", "text/markdown")}
        assert c.post("/api/knowledge/upload", files=mine, data={"scope": "mine"}, headers=hb).json()["errors"] == []
        team = {"files": ("party.md", b"The team holiday party is on December 12.", "text/markdown")}
        assert c.post("/api/knowledge/upload", files=team, data={"scope": "team"}, headers=hb).status_code == 403
        assert c.post("/api/knowledge/upload", files=team, data={"scope": "team"}, headers=ha).status_code == 200
        c.post("/api/knowledge/sync", json={"scope": "mine"}, headers=hb)
        c.post("/api/knowledge/sync", json={"scope": "team"}, headers=ha)

        # Personal documents are private; team documents are visible to everyone.
        r = c.get("/api/knowledge", params={"scope": "mine"}, headers=hb)
        assert names(r) == ["bob.md"] and r.json()["can_manage"]
        assert names(c.get("/api/knowledge", params={"scope": "mine"}, headers=hc)) == []
        r = c.get("/api/knowledge", params={"scope": "team"}, headers=hb)
        assert names(r) == ["party.md"] and not r.json()["can_manage"] and r.json()["folder"] == ""
        assert (data / "users" / store.by_name("Bob").id / "knowledge_files" / "bob.md").exists()

        # Replies search the user's own documents plus the team's, labelled for References.
        state = c.app.state.cok
        bob, cy = store.by_name("Bob"), store.by_name("Cy")
        hits, err = c.portal.call(chat.search_knowledge, state, state.ctx(bob), "What is the project codename?")
        assert not err and [h.rel_path for h in hits] == ["My documents/bob.md"]
        hits, _ = c.portal.call(chat.search_knowledge, state, state.ctx(cy), "What is the project codename?")
        assert hits == []  # Cy can't find Bob's documents
        hits, _ = c.portal.call(chat.search_knowledge, state, state.ctx(cy), "When is the holiday party?")
        assert [h.rel_path for h in hits] == ["Team documents/party.md"]

        # Users delete their own files; deleting team files needs an admin.
        fid = c.get("/api/knowledge", params={"scope": "team"}, headers=hb).json()["files"][0]["id"]
        assert c.delete(f"/api/knowledge/files/{fid}", params={"scope": "team"}, headers=hb).status_code == 403
        fid = c.get("/api/knowledge", params={"scope": "mine"}, headers=hb).json()["files"][0]["id"]
        assert c.delete(f"/api/knowledge/files/{fid}", params={"scope": "mine"}, headers=hb).status_code == 200
        assert names(c.get("/api/knowledge", params={"scope": "mine"}, headers=hb)) == []


def test_review_fixes(data, monkeypatch):
    """Regression tests for issues found in code review."""
    with client("local", monkeypatch, base="http://127.0.0.1:8765") as c:
        # Sandboxed iframes / local files send Origin: null - local mode has no login, so refuse.
        assert c.post("/api/memory", json={"text": "x"}, headers={"Origin": "null"}).status_code == 403
        assert c.post("/api/memory", json={"text": "x"}, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
        assert c.post("/api/memory", json={"text": "ok"}, headers={"Origin": "http://127.0.0.1:8765"}).status_code == 200
        # Malformed chat requests are rejected cleanly instead of crashing.
        assert c.post("/api/chat", json={"content": 5}).status_code == 422
        assert c.post("/api/chat", json={"content": "hi", "attachments": "nope"}).status_code == 422
        bad_image = {"content": "hi", "attachments": [{"name": "x.png", "kind": "image", "data": "data:text/html,<b>"}]}
        assert c.post("/api/chat", json=bad_image).status_code == 400


def test_mcp_rename_clash_keeps_both(tmp_path, monkeypatch):
    import asyncio

    from cokcopilot.mcp_manager import MCPConfigError, MCPManager

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)

    async def go():
        mgr = MCPManager(tmp_path / "mcp.json")
        await mgr.upsert("a", {"command": "nope-a", "disabled": True})
        await mgr.upsert("b", {"command": "nope-b", "disabled": True})
        try:
            await mgr.upsert("b", {"command": "nope-a", "disabled": True}, old_name="a")
        except MCPConfigError:
            pass
        assert sorted(mgr.servers) == ["a", "b"]

    asyncio.run(go())


def test_docx_zip_bomb_and_old_dark_mode(tmp_path):
    import zipfile

    from cokcopilot.models import Settings
    from cokcopilot.parsers import MAX_DOCX_XML_BYTES, extract_document_text

    p = tmp_path / "bomb.docx"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", b" " * (MAX_DOCX_XML_BYTES + 1))
    assert p.stat().st_size < 1_000_000
    with pytest.raises(ValueError):
        extract_document_text(p)
    assert Settings.from_dict({"dark_mode": True}).theme == "dark"
    assert Settings.from_dict({"dark_mode": True, "theme": "light"}).theme == "light"


def test_groups_invites_shared_chats_and_knowledge(data, monkeypatch):
    from cokcopilot import chat
    from cokcopilot.models import Conversation

    store = UserStore()
    _, ann_key = store.add("Ann", "admin")
    _, bob_key = store.add("Bob")
    _, cy_key = store.add("Cy")
    _, dee_key = store.add("Dee")
    ha, hb, hc, hd = ({"Authorization": f"Bearer {k}"} for k in (ann_key, bob_key, cy_key, dee_key))
    bob, cy = store.by_name("Bob"), store.by_name("Cy")

    with client("server", monkeypatch) as c:
        # Bob creates a group and invites Cy; Cy only joins after accepting.
        g = c.post("/api/groups", json={"name": "  Launch   team "}, headers=hb).json()
        gid = g["id"]
        assert g["name"] == "Launch team" and g["is_owner"]
        assert c.post(f"/api/groups/{gid}/invites", json={"name": "Nobody"}, headers=hb).status_code == 400
        assert c.post(f"/api/groups/{gid}/invites", json={"name": "cy"}, headers=hb).json()["invites"][0]["name"] == "Cy"
        assert c.post(f"/api/groups/{gid}/invites", json={"name": "Dee"}, headers=hc).status_code == 404  # not a member
        r = c.get("/api/groups", headers=hc).json()
        assert r["groups"] == [] and [x["name"] for x in r["invites"]] == ["Launch team"]
        assert "invites" not in r["invites"][0]  # only the owner sees who else is invited
        assert c.get(f"/api/conversations?group_id={gid}", headers=hc).status_code == 404
        assert c.post(f"/api/groups/{gid}/respond", json={"accept": True}, headers=hc).status_code == 200
        r = c.get("/api/groups", headers=hc).json()
        assert r["invites"] == [] and [m["name"] for m in r["groups"][0]["members"]] == ["Bob", "Cy"]
        assert not r["groups"][0]["is_owner"]
        # Dee declines; only the owner can invite.
        assert c.post(f"/api/groups/{gid}/invites", json={"name": "Dee"}, headers=hc).status_code == 403
        c.post(f"/api/groups/{gid}/invites", json={"name": "Dee"}, headers=hb)
        c.post(f"/api/groups/{gid}/respond", json={"accept": False}, headers=hd)
        assert c.get("/api/groups", headers=hd).json() == {"groups": [], "invites": []}

        # Any member adds group documents; outsiders can't see them.
        doc = {"files": ("launch.md", b"The launch date for Project Falcon is March 3.", "text/markdown")}
        r = c.post("/api/knowledge/upload", files=doc, data={"scope": "group", "group_id": gid}, headers=hc)
        assert r.json()["errors"] == []
        c.post("/api/knowledge/sync", json={"scope": "group", "group_id": gid}, headers=hc)
        r = c.get("/api/knowledge", params={"scope": "group", "group_id": gid}, headers=hb).json()
        assert [f["rel_path"] for f in r["files"]] == ["launch.md"] and r["can_manage"]
        assert c.get("/api/knowledge", params={"scope": "group", "group_id": gid}, headers=hd).status_code == 404
        assert c.post("/api/knowledge/upload", files=doc, data={"scope": "group", "group_id": gid},
                      headers=ha).status_code == 404  # even an admin, unless a member

        # Group chats search the group's documents (not anyone's private ones) and the team's.
        my_doc = {"files": ("bob.md", b"Bob's secret launch date guess is June 9.", "text/markdown")}
        c.post("/api/knowledge/upload", files=my_doc, data={"scope": "mine"}, headers=hb)
        state = c.app.state.cok
        gctx = state.group_ctx(state.ctx(bob), state.groups.get(gid))
        hits, err = c.portal.call(chat.search_knowledge, state, gctx, "When is the launch date for Project Falcon?")
        assert not err and [h.rel_path for h in hits] == ["Launch team/launch.md"]
        hits, _ = c.portal.call(chat.search_knowledge, state, state.ctx(cy), "When is the launch date for Project Falcon?")
        assert hits == []  # outside the group, Cy's chats don't search the group's documents

        # Every member sees every group chat; only its author changes it, the owner may delete it.
        convo = Conversation(title="Plan", group_id=gid, user_id=cy.id, user_name="Cy")
        config.save_conversation(convo, data / "groups" / gid / "conversations")
        assert [x["user_name"] for x in c.get(f"/api/conversations?group_id={gid}", headers=hb).json()] == ["Cy"]
        assert c.get(f"/api/conversations/{convo.id}?group_id={gid}", headers=hb).json()["title"] == "Plan"
        assert c.get(f"/api/conversations/{convo.id}", headers=hb).status_code == 404  # not in personal chats
        assert c.get(f"/api/conversations/{convo.id}?group_id={gid}", headers=hd).status_code == 404
        assert c.patch(f"/api/conversations/{convo.id}?group_id={gid}", json={"title": "x"}, headers=hb).status_code == 403
        assert c.post("/api/chat", json={"conversation_id": convo.id, "group_id": gid, "content": "hi"},
                      headers=hb).status_code == 403
        assert c.patch(f"/api/conversations/{convo.id}?group_id={gid}", json={"title": "Plan B"},
                       headers=hc).status_code == 200
        assert c.delete(f"/api/conversations/{convo.id}?group_id={gid}", headers=hb).status_code == 200

        # Members leave; the owner can't leave, only delete the group (which removes its data).
        assert c.delete(f"/api/groups/{gid}/members/{bob.id}", headers=hb).status_code == 400
        assert c.delete(f"/api/groups/{gid}/members/{bob.id}", headers=hc).status_code == 403
        assert c.delete(f"/api/groups/{gid}", headers=hc).status_code == 403
        assert c.delete(f"/api/groups/{gid}/members/{cy.id}", headers=hc).json() == {"ok": True}
        assert c.get("/api/groups", headers=hc).json()["groups"] == []
        assert c.delete(f"/api/groups/{gid}", headers=hb).status_code == 200
        assert not (data / "groups" / gid).exists()

        # Removing a user hands their groups on to the next member.
        g2 = c.post("/api/groups", json={"name": "G2"}, headers=hb).json()["id"]
        c.post(f"/api/groups/{g2}/invites", json={"name": "Cy"}, headers=hb)
        c.post(f"/api/groups/{g2}/respond", json={"accept": True}, headers=hc)
        assert c.delete(f"/api/users/{bob.id}", headers=ha).status_code == 200
        g = c.get("/api/groups", headers=hc).json()["groups"][0]
        assert g["is_owner"] and [m["name"] for m in g["members"]] == ["Cy"]


def test_groups_need_server_mode(data, monkeypatch):
    with client("local", monkeypatch, base="http://127.0.0.1:8765") as c:
        assert c.get("/api/groups").status_code == 400
        assert c.get("/api/people").json() == []


def test_new_group_chat_is_saved_in_the_group(data, monkeypatch):
    store = UserStore()
    _, bob_key = store.add("Bob", "admin")
    _, cy_key = store.add("Cy")
    hb, hc = ({"Authorization": f"Bearer {k}"} for k in (bob_key, cy_key))
    with client("server", monkeypatch) as c:
        gid = c.post("/api/groups", json={"name": "G"}, headers=hb).json()["id"]
        c.post(f"/api/groups/{gid}/invites", json={"name": "Cy"}, headers=hb)
        c.post(f"/api/groups/{gid}/respond", json={"accept": True}, headers=hc)
        # The model API is unreachable in tests; the chat is saved before the reply is attempted.
        r = c.post("/api/chat", json={"group_id": gid, "content": "hello group"}, headers=hc)
        assert r.status_code == 200 and '"type": "start"' in r.text
        chats = c.get(f"/api/conversations?group_id={gid}", headers=hb).json()
        assert [(x["title"], x["user_name"], x["group_id"]) for x in chats] == [("hello group", "Cy", gid)]
        assert c.get("/api/conversations", headers=hc).json() == []  # not among Cy's personal chats
