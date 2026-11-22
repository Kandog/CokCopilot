"""Server mode: Microsoft Entra ID sign-in, the usage dashboard API, and linked shared knowledge folders."""

import time
from urllib.parse import parse_qs, urlparse

import pytest

from cokcopilot import config, sso
from cokcopilot.auth import UserStore
from cokcopilot.usage import Usage, UsageLog

from test_server import client, data  # noqa: F401 - fixtures

TENANT = "11111111-2222-3333-4444-555555555555"
CLIENT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _sso_on(tmp, **extra):
    config.write_json(tmp / "sso.json", {"enabled": True, "tenant_id": TENANT, "client_id": CLIENT,
                                         "client_secret": "s3cret", **extra})


def _claims(nonce, **over):
    now = time.time()
    return {"tid": TENANT, "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0", "aud": CLIENT,
            "exp": now + 3600, "nbf": now - 10, "nonce": nonce, "oid": "oid-alice",
            "preferred_username": "Alice@Company.com", **over}


def _sign_in(c, monkeypatch, claims_fn):
    r = c.get("/api/auth/sso/start", follow_redirects=False)
    assert r.status_code == 303
    url = urlparse(r.headers["location"])
    assert url.netloc == "login.microsoftonline.com" and url.path.startswith(f"/{TENANT}/")
    q = parse_qs(url.query)
    assert q["code_challenge_method"] == ["S256"] and q["client_id"] == [CLIENT]
    assert q["redirect_uri"] == ["https://chat.example.com/api/auth/sso/callback"]
    seen = {}

    async def fake_redeem(cfg, code, redirect_uri, verifier, nonce):
        seen.update(code=code, verifier=verifier)
        claims = claims_fn(nonce)
        sso.check_claims(cfg, claims, nonce)
        return claims

    monkeypatch.setattr(sso, "redeem", fake_redeem)
    r = c.get(f"/api/auth/sso/callback?code=abc&state={q['state'][0]}", follow_redirects=False,
              headers={"sec-fetch-site": "cross-site"})
    assert seen.get("code") in (None, "abc")
    return r


def test_sso_signs_in_existing_users_only(data, monkeypatch):
    store = UserStore()
    _, admin_key = store.add("Ann", "admin")
    alice, alice_key = store.add("Alice", email="alice@company.com")
    _sso_on(data)

    with client("server", monkeypatch) as c:
        assert c.get("/api/auth/options").json() == {"mode": "server", "sso": True,
                                                     "sso_label": "Sign in with Microsoft", "keys": "admins"}
        # Keys are off for users while SSO is on; admins keep theirs as a way back in.
        assert c.post("/api/auth/login", json={"key": alice_key}).status_code == 403
        assert c.get("/api/me", headers={"Authorization": f"Bearer {alice_key}"}).status_code == 401

        r = _sign_in(c, monkeypatch, _claims)
        assert r.status_code == 303 and r.headers["location"] == "/"
        me = c.get("/api/me").json()
        assert me["name"] == "Alice" and me["sso"] is True
        assert UserStore().get(alice.id).sso_oid == "oid-alice"  # bound: later matched by object id

    with client("server", monkeypatch) as c:
        r = _sign_in(c, monkeypatch, lambda n: _claims(n, oid="oid-mallory", preferred_username="mallory@company.com"))
        assert "sso_error=" in r.headers["location"] and "doesn%27t%20have%20access" in r.headers["location"]
        assert c.get("/api/me").status_code == 401

        r = _sign_in(c, monkeypatch, lambda n: _claims(n, tid="99999999-2222-3333-4444-555555555555"))
        assert "different%20organization" in r.headers["location"]

        # A callback without the matching state cookie is refused.
        c.cookies.clear()
        r = c.get("/api/auth/sso/callback?code=abc&state=forged", follow_redirects=False)
        assert "sso_error=" in r.headers["location"]

        assert c.post("/api/auth/login", json={"key": admin_key}).status_code == 200


def test_turning_sso_on_ends_user_key_sessions(data, monkeypatch):
    store = UserStore()
    _, admin_key = store.add("Ann", "admin")
    _, bob_key = store.add("Bob")
    with client("server", monkeypatch) as c:
        assert c.post("/api/auth/login", json={"key": bob_key}).status_code == 200
        assert c.get("/api/me").status_code == 200
        _sso_on(data)
        assert c.get("/api/me").status_code == 401
        _sso_on(data, allow_access_keys=True)
        assert c.post("/api/auth/login", json={"key": bob_key}).status_code == 200


def test_admin_sso_settings_hide_secret_and_validate(data, monkeypatch):
    _, admin_key = UserStore().add("Ann", "admin")
    _, bob_key = UserStore().add("Bob")
    with client("server", monkeypatch) as c:
        assert c.get("/api/admin/sso", headers={"Authorization": f"Bearer {bob_key}"}).status_code == 403
        h = {"Authorization": f"Bearer {admin_key}"}
        assert c.put("/api/admin/sso", json={"enabled": True, "tenant_id": "common"}, headers=h).status_code == 400
        r = c.put("/api/admin/sso", headers=h, json={"enabled": True, "tenant_id": TENANT, "client_id": CLIENT,
                                                     "client_secret": "s3cret"})
        assert r.status_code == 200
        body = r.json()
        assert body["client_secret"] == "" and body["client_secret_set"] and body["enabled"]
        assert body["default_redirect_uri"] == "https://chat.example.com/api/auth/sso/callback"
        # Saving without a secret keeps the saved one.
        assert c.put("/api/admin/sso", json={"button_label": "Company sign-in"}, headers=h).json()["client_secret_set"]
        assert "s3cret" not in c.get("/api/admin/sso", headers=h).text


def test_usage_dashboard_is_admin_only(data, monkeypatch):
    store = UserStore()
    admin, admin_key = store.add("Ann", "admin")
    bob, bob_key = store.add("Bob")
    log = UsageLog(data / "usage")
    u = Usage()
    u.add({"prompt_tokens": 50, "completion_tokens": 10}, "", "")
    log.record(bob.id, "Bob", "gpt-4o", u)
    log.record(bob.id, "Bob", "gpt-4o", u)
    with client("server", monkeypatch) as c:
        assert c.get("/api/usage", headers={"Authorization": f"Bearer {bob_key}"}).status_code == 403
        h = {"Authorization": f"Bearer {admin_key}"}
        r = c.get("/api/usage?days=7", headers=h).json()
        assert r["totals"]["requests"] == 2 and r["totals"]["total_tokens"] == 120
        assert r["users"][0]["name"] == "Bob" and r["users"][0]["top_model"] == "gpt-4o"
        assert c.get(f"/api/usage?days=7&user_id={admin.id}", headers=h).json()["totals"]["requests"] == 0
        csv = c.get("/api/usage/export?days=7", headers=h)
        assert csv.status_code == 200 and "Bob,2,100,20,120" in csv.text


@pytest.fixture
def share(data):
    root = data.parent / f"{data.name}-share"  # outside the data folder, like a real share
    (root / "team" / "sub" / "deeper").mkdir(parents=True)
    (root / "team" / "a.md").write_text("alpha project notes", encoding="utf-8")
    (root / "team" / "sub" / "deeper" / "b.txt").write_text("bravo budget figures", encoding="utf-8")
    (data.parent / f"{data.name}-outside").mkdir()
    cfg = config.read_json(data / "config.json")
    config.write_json(data / "config.json", {**cfg, "knowledge_roots": str(root)})
    return root


def test_link_shared_folder_indexes_subfolders(data, share, monkeypatch):
    store = UserStore()
    _, ann_key = store.add("Ann", "admin")
    _, bob_key = store.add("Bob")
    hb = {"Authorization": f"Bearer {bob_key}"}
    with client("server", monkeypatch) as c:
        kb = c.get("/api/knowledge?scope=mine", headers=hb).json()
        assert kb["can_link"] and not kb["linked"]
        roots = c.get("/api/knowledge/browse", headers=hb).json()
        assert roots["dirs"][0]["path"] == str(share)
        inside = c.get("/api/knowledge/browse", params={"path": str(share)}, headers=hb).json()
        assert [d["name"] for d in inside["dirs"]] == ["team"]

        for bad in (str(data.parent / f"{data.name}-outside"), str(data), str(share / "missing")):
            assert c.post("/api/knowledge/link", json={"scope": "mine", "folder": bad}, headers=hb).status_code == 400

        r = c.post("/api/knowledge/link", json={"scope": "mine", "folder": str(share / "team")}, headers=hb)
        assert r.status_code == 200 and r.json()["linked"]
        c.post("/api/knowledge/sync", json={"scope": "mine"}, headers=hb)
        files = c.get("/api/knowledge?scope=mine", headers=hb).json()["files"]
        assert sorted(f["rel_path"] for f in files) == ["a.md", "sub/deeper/b.txt"]
        # The folder is the source of truth: no uploads or deletes through the app.
        r = c.post("/api/knowledge/upload", data={"scope": "mine"}, files={"files": ("x.txt", b"x")}, headers=hb)
        assert r.status_code == 400
        assert c.delete(f"/api/knowledge/files/{files[0]['id']}?scope=mine", headers=hb).status_code == 400

        # Groups: only the owner links a folder.
        ha = {"Authorization": f"Bearer {ann_key}"}
        g = c.post("/api/groups", json={"name": "Finance"}, headers=ha).json()
        c.post(f"/api/groups/{g['id']}/invites", json={"name": "Bob"}, headers=ha)
        c.post(f"/api/groups/{g['id']}/respond", json={"accept": True}, headers=hb)
        link = {"scope": "group", "group_id": g["id"], "folder": str(share / "team" / "sub")}
        assert c.post("/api/knowledge/link", json=link, headers=hb).status_code == 403
        assert c.post("/api/knowledge/link", json=link, headers=ha).json()["linked"]
        c.post("/api/knowledge/sync", json={"scope": "group", "group_id": g["id"]}, headers=hb)
        gk = c.get(f"/api/knowledge?scope=group&group_id={g['id']}", headers=hb).json()
        assert [f["rel_path"] for f in gk["files"]] == ["deeper/b.txt"] and not gk["can_link"]

        # Unlinking goes back to uploaded documents.
        r = c.post("/api/knowledge/link", json={"scope": "mine", "folder": ""}, headers=hb)
        assert not r.json()["linked"]
        assert c.post("/api/knowledge/upload", data={"scope": "mine"}, files={"files": ("x.txt", b"xray")},
                      headers=hb).status_code == 200


def test_linking_needs_admin_roots(data, monkeypatch):
    _, bob_key = UserStore().add("Bob")
    (data / "share").mkdir()
    with client("server", monkeypatch) as c:
        h = {"Authorization": f"Bearer {bob_key}"}
        assert not c.get("/api/knowledge?scope=mine", headers=h).json()["can_link"]
        r = c.post("/api/knowledge/link", json={"scope": "mine", "folder": str(data / "share")}, headers=h)
        assert r.status_code == 400 and "hasn't allowed" in r.json()["detail"]
