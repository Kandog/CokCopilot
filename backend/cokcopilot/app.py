"""FastAPI app: JSON API under /api, plus the built frontend at /.

Two modes (see ``state.AppState.mode``):
- local: one user, no login, only reachable from this computer (127.0.0.1).
- server: every /api request needs a user (session cookie or ``Authorization: Bearer <key>``);
  admins manage the shared setup, everyone else chats with their own history and memory.
"""

from __future__ import annotations

import asyncio
import base64
import hmac
import os
import sys
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote, urlparse

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field

from . import __version__, chat, config, llm, parsers, sso
from .auth import LOCAL_USER, METHOD_KEY, METHOD_SSO, SESSION_COOKIE, SESSION_DAYS, AuthError, User
from .groups import Group, GroupError
from .mcp_manager import MCPConfigError
from .models import Settings, new_id, now_iso
from .skills import SkillError, SkillExists
from .state import AppState, UserCtx

FRONTEND_DIST = Path(os.environ.get("COKCOPILOT_FRONTEND_DIST", config.REPO_ROOT / "frontend" / "dist"))
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
MAX_DOC_CHARS = 200_000
DEV_ORIGINS = {"http://localhost:5173", "http://127.0.0.1:5173"}
PUBLIC_PATHS = {"/api/health", "/api/auth/login", "/api/auth/logout", "/api/auth/options",
                "/api/auth/sso/start", sso.CALLBACK_PATH}
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    state = AppState.load()
    app.state.cok = state
    await state.start()
    try:
        yield
    finally:
        await state.shutdown()


app = FastAPI(title="CokCopilot", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None)


def S(request: Request) -> AppState:
    return request.app.state.cok


def U(request: Request) -> User:
    return request.state.user


def C(request: Request) -> UserCtx:
    return S(request).ctx(U(request))


def admin(request: Request) -> UserCtx:
    ctx = C(request)
    if not ctx.user.is_admin:
        raise HTTPException(403, "only an admin can do this")
    return ctx


def local_only(request: Request) -> None:
    if S(request).is_server:
        raise HTTPException(400, "not available when CokCopilot runs on a server")


def _allowed_origins() -> set[str]:
    extra = os.environ.get("COKCOPILOT_ALLOWED_ORIGINS", "")
    out = {o.strip().rstrip("/") for o in extra.split(",") if o.strip()}
    if os.environ.get("COKCOPILOT_DEV"):
        out |= DEV_ORIGINS
    return out


def _same_origin(request: Request, origin: str) -> bool:
    if origin == "null":
        return False
    o = urlparse(origin)
    hosts = {request.headers.get("host", ""), request.headers.get("x-forwarded-host", "")}
    return bool(o.netloc) and o.netloc.lower() in {h.lower() for h in hosts if h}


def _user_from_request(state: AppState, request: Request) -> User | None:
    assert state.users is not None
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        user, method = state.users.authenticate_key(auth[7:].strip()), METHOD_KEY
    else:
        token = request.cookies.get(SESSION_COOKIE)
        found = state.users.check_session(token) if token else None
        user, method = found if found else (None, "")
    # Turning SSO on also ends the key sessions of users who must now use SSO.
    if user and method == METHOD_KEY and not state.key_login_allowed(user):
        return None
    return user


@app.middleware("http")
async def security(request: Request, call_next):
    if not request.url.path.startswith("/api"):
        return await call_next(request)
    state: AppState = request.app.state.cok
    # Cross-site request protection: browsers always send Origin on cross-site
    # POSTs, so anything that isn't this site (or an allowed origin) is refused.
    # "null" (sandboxed iframes, local files) counts as foreign too, and so does
    # any request the browser itself labels cross-site.
    origin = request.headers.get("origin")
    if origin is not None:
        if not _same_origin(request, origin) and origin.rstrip("/") not in _allowed_origins():
            return JSONResponse({"detail": "cross-origin request blocked"}, status_code=403)
    elif request.headers.get("sec-fetch-site") == "cross-site" and request.url.path != sso.CALLBACK_PATH:
        # (Microsoft's redirect back after sign-in is cross-site by nature; state + PKCE protect it.)
        return JSONResponse({"detail": "cross-origin request blocked"}, status_code=403)
    if not state.is_server:
        # Local mode has no login, so it must only be reachable from this computer
        # (the Host check also stops DNS-rebinding attacks).
        host = (request.headers.get("host") or "").rsplit(":", 1)[0]
        if host not in LOCAL_HOSTS:
            return JSONResponse({"detail": "forbidden host"}, status_code=403)
        request.state.user = LOCAL_USER
    else:
        user = _user_from_request(state, request)
        if user is None and request.url.path not in PUBLIC_PATHS:
            return JSONResponse({"detail": "not signed in"}, status_code=401)
        request.state.user = user
    return await call_next(request)


# -- auth & users ---------------------------------------------------------------


@app.get("/api/health")
async def health(request: Request):
    s = S(request)
    out: dict[str, Any] = {"ok": True, "version": __version__, "mode": s.mode, "app": "cokcopilot"}
    if not s.is_server:
        out["data_dir"] = str(config.DATA_DIR)
    return out


@app.get("/api/me")
async def me(request: Request):
    s, u = S(request), U(request)
    return {**u.public(), "is_admin": u.is_admin, "mode": s.mode, "version": __version__,
            "sso": s.is_server and s.sso_on()}


@app.post("/api/auth/login")
async def login(request: Request, body: dict[str, Any] = Body(...)):
    s = S(request)
    if not s.is_server:
        return {"ok": True}
    user = s.users.authenticate_key(str(body.get("key", "")).strip())
    if not user:
        await asyncio.sleep(0.7)  # slow down guessing
        raise HTTPException(401, "That access key isn't valid. Ask your admin for a new one.")
    if not s.key_login_allowed(user):
        raise HTTPException(403, "Access keys are turned off on this server. Use \"Sign in with Microsoft\".")
    resp = JSONResponse({**user.public(), "is_admin": user.is_admin})
    _set_session(request, resp, s.users.make_session(user, METHOD_KEY))
    return resp


def _set_session(request: Request, resp, token: str) -> None:
    resp.set_cookie(SESSION_COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True,
                    secure=_is_https(request), samesite="strict", path="/")


@app.get("/api/auth/options")
async def auth_options(request: Request):
    """What the sign-in screen offers."""
    s = S(request)
    on = s.is_server and s.sso_on()
    cfg = s.sso.config if s.sso else None
    return {"mode": s.mode, "sso": on, "sso_label": cfg.button_label if (on and cfg) else "",
            # "admins": SSO is on and keys are off, so the key box is only a way back in for admins.
            "keys": "all" if not on or cfg.allow_access_keys else "admins"}


# -- single sign-on (Microsoft Entra ID / Azure AD) --


def _public_origin(request: Request) -> str:
    allowed = sorted(o for o in _allowed_origins() if o.startswith("https://"))
    if allowed:
        return allowed[0]
    host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
    return f"{'https' if _is_https(request) else request.url.scheme}://{host}"


def _redirect_uri(request: Request) -> str:
    return S(request).sso.config.redirect_uri or _public_origin(request) + sso.CALLBACK_PATH


def _sso_failed(message: str) -> RedirectResponse:
    resp = RedirectResponse(f"/?sso_error={quote(message[:300])}", status_code=303)
    resp.delete_cookie(sso.STATE_COOKIE, path="/api/auth/sso")
    return resp


@app.get("/api/auth/sso/start")
async def sso_start(request: Request):
    s = S(request)
    if not s.is_server or not s.sso_on():
        return _sso_failed("Single sign-on isn't turned on.")
    login = sso.new_login()
    resp = RedirectResponse(sso.authorize_url(s.sso.config, _redirect_uri(request), login), status_code=303)
    value = "~".join((login["state"], login["nonce"], login["verifier"], login["exp"]))
    # Lax, not Strict: it has to come back with Microsoft's (cross-site) redirect to the callback.
    resp.set_cookie(sso.STATE_COOKIE, s.users.sign_value(value), max_age=sso.STATE_MAX_AGE, httponly=True,
                    secure=_is_https(request), samesite="lax", path="/api/auth/sso")
    return resp


@app.get(sso.CALLBACK_PATH)
async def sso_callback(request: Request, code: str = "", state: str = "", error: str = "",
                       error_description: str = ""):
    s = S(request)
    if not s.is_server or not s.sso_on():
        return _sso_failed("Single sign-on isn't turned on.")
    if error:
        return _sso_failed(error_description.splitlines()[0] if error_description else error)
    saved = s.users.unsign_value(request.cookies.get(sso.STATE_COOKIE, ""))
    parts = saved.split("~") if saved else []
    if (len(parts) != 4 or not code or not state or not hmac.compare_digest(parts[0].encode(), state.encode())
            or int(parts[3]) < time.time()):
        return _sso_failed("The sign-in took too long or was started in another window. Please try again.")
    _, nonce, verifier, _ = parts
    try:
        claims = await sso.redeem(s.sso.config, code, _redirect_uri(request), verifier, nonce)
    except sso.SSOError as e:
        return _sso_failed(f"Microsoft sign-in failed: {e}")
    emails = sso.claim_emails(claims)
    user = s.users.sso_user(str(claims["oid"]), emails)
    if not user:
        who = emails[0] if emails else "This account"
        return _sso_failed(f"{who} doesn't have access to CokCopilot. Ask your admin to add you.")
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie(sso.STATE_COOKIE, path="/api/auth/sso")
    _set_session(request, resp, s.users.make_session(user, METHOD_SSO))
    return resp


def _sso_store(request: Request):
    _users(request)  # admins, server mode
    return S(request).sso


@app.get("/api/admin/sso")
async def get_sso(request: Request):
    return {**_sso_store(request).reload().public(),
            "default_redirect_uri": _public_origin(request) + sso.CALLBACK_PATH}


@app.put("/api/admin/sso")
async def put_sso(request: Request, body: dict[str, Any] = Body(...)):
    try:
        _sso_store(request).save(body)
    except sso.SSOError as e:
        raise HTTPException(400, str(e)) from e
    return await get_sso(request)


@app.post("/api/admin/sso/check")
async def check_sso(request: Request, body: dict[str, Any] | None = Body(None)):
    """Ask Microsoft whether the tenant ID exists (from the server, so it also tests the server's network)."""
    store = _sso_store(request)
    tenant = str((body or {}).get("tenant_id") or store.reload().tenant_id).strip()
    try:
        return {"ok": True, "issuer": await sso.check_tenant(tenant)}
    except sso.SSOError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/auth/logout")
async def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


def _is_https(request: Request) -> bool:
    # Behind IIS the app itself sees plain HTTP; the browser's Origin still tells us the page is HTTPS.
    return (request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "") == "https"
            or request.headers.get("origin", "").startswith("https://"))


def _users(request: Request):
    admin(request)
    s = S(request)
    if not s.is_server:
        raise HTTPException(400, "user accounts exist only when CokCopilot runs as a server")
    return s.users


@app.get("/api/users")
async def list_users(request: Request):
    return [u.public() for u in _users(request).list()]


@app.post("/api/users")
async def add_user(request: Request, body: dict[str, Any] = Body(...)):
    try:
        user, key = _users(request).add(str(body.get("name", "")), str(body.get("role", "user")),
                                        str(body.get("email") or ""))
    except AuthError as e:
        raise HTTPException(400, str(e)) from e
    return {"user": user.public(), "key": key}


@app.post("/api/users/{user_id}/reset-key")
async def reset_user_key(request: Request, user_id: str):
    try:
        return {"key": _users(request).reset_key(user_id)}
    except AuthError as e:
        raise HTTPException(400, str(e)) from e


@app.patch("/api/users/{user_id}")
async def update_user(request: Request, user_id: str, body: dict[str, Any] = Body(...)):
    try:
        u = _users(request).update(user_id, role=body.get("role"), disabled=body.get("disabled"),
                                   name=body.get("name"), email=body.get("email"))
    except AuthError as e:
        raise HTTPException(400, str(e)) from e
    return u.public()


@app.delete("/api/users/{user_id}")
async def delete_user(request: Request, user_id: str):
    try:
        _users(request).remove(user_id)
    except AuthError as e:
        raise HTTPException(400, str(e)) from e
    S(request).groups.forget_user(user_id)
    return {"ok": True}


@app.get("/api/usage")
async def usage_summary(request: Request, days: int = 30, user_id: str | None = None):
    """Admins: requests and tokens per user, per model and per day (``user_id`` narrows totals and charts)."""
    admin(request)
    s = S(request)
    names = {u.id: u.name for u in s.users.list()} if s.is_server else {LOCAL_USER.id: LOCAL_USER.name}
    return await asyncio.to_thread(s.usage.summary, days, user_id or None, names)


@app.get("/api/usage/export")
async def usage_export(request: Request, days: int = 30):
    """Admins: the per-user table as CSV."""
    import csv
    import io

    data = await usage_summary(request, days)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["user", "requests", "prompt_tokens", "completion_tokens", "total_tokens", "tool_calls",
                "errors", "top_model", "last_active", "estimated"])
    for u in data["users"]:
        w.writerow([u["name"], u["requests"], u["prompt_tokens"], u["completion_tokens"], u["total_tokens"],
                    u["tool_calls"], u["errors"], u["top_model"], u["last_active"], u["estimated"]])
    return PlainTextResponse(buf.getvalue(), media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="cokcopilot-usage-{data["since"]}-to-{data["until"]}.csv"'})


@app.get("/api/people")
async def people(request: Request):
    """Names of active users, so anyone can pick whom to invite to a group (server mode)."""
    s = S(request)
    if not s.is_server:
        return []
    return [{"id": u.id, "name": u.name} for u in s.users.list() if not u.disabled]


# -- groups (server mode) ------------------------------------------------------------
# Members share the group's chats (everyone reads them; whoever started a chat continues it)
# and the group's knowledge base (everyone adds and removes documents).


def _groups(request: Request):
    s = S(request)
    if not s.is_server:
        raise HTTPException(400, "groups exist only when CokCopilot runs as a server")
    return s.groups


def _member_group(request: Request, group_id: str) -> Group:
    g = _groups(request).get(group_id)
    if not g or not g.is_member(U(request).id):
        raise HTTPException(404, "group not found")
    return g


def _group_public(request: Request, g: Group) -> dict[str, Any]:
    s, me_id = S(request), U(request).id
    names = {u.id: u.name for u in s.users.list()}
    out: dict[str, Any] = {
        "id": g.id, "name": g.name, "owner_id": g.owner_id, "owner_name": names.get(g.owner_id, ""),
        "created_at": g.created_at, "is_owner": g.is_owner(me_id), "is_member": g.is_member(me_id),
        "members": [{"id": m, "name": names[m], "is_owner": g.is_owner(m)} for m in g.members if m in names],
    }
    if g.is_owner(me_id):
        out["invites"] = [{"id": i, "name": names[i]} for i in g.invites if i in names]
    return out


def _group_call(fn, *args):
    try:
        return fn(*args)
    except GroupError as e:
        msg = str(e)
        raise HTTPException(404 if msg == "group not found" else 403 if msg.startswith("only") else 400, msg) from e


@app.get("/api/groups")
async def list_groups(request: Request):
    store, me_id = _groups(request), U(request).id
    return {
        "groups": [_group_public(request, g) for g in store.for_user(me_id)],
        "invites": [_group_public(request, g) for g in store.invites_for(me_id)],
    }


@app.post("/api/groups")
async def create_group(request: Request, body: dict[str, Any] = Body(...)):
    g = _group_call(_groups(request).create, str(body.get("name", "")), U(request).id)
    return _group_public(request, g)


@app.patch("/api/groups/{group_id}")
async def rename_group(request: Request, group_id: str, body: dict[str, Any] = Body(...)):
    g = _group_call(_groups(request).rename, group_id, U(request).id, str(body.get("name", "")))
    return _group_public(request, g)


@app.delete("/api/groups/{group_id}")
async def delete_group(request: Request, group_id: str):
    s = S(request)
    if any(st.group_id == group_id and st.task and not st.task.done() for st in s.streams.values()):
        raise HTTPException(409, "a reply is being written in this group; try again in a moment")
    _group_call(_groups(request).delete, group_id, U(request).id)
    s.delete_group_data(group_id)
    return {"ok": True}


@app.post("/api/groups/{group_id}/invites")
async def invite_to_group(request: Request, group_id: str, body: dict[str, Any] = Body(...)):
    s = S(request)
    _member_group(request, group_id)
    invitee = s.users.get(str(body.get("user_id", ""))) or s.users.by_name(str(body.get("name", "")))
    if not invitee or invitee.disabled:
        raise HTTPException(400, "there's no user with that name")
    g = _group_call(_groups(request).invite, group_id, U(request).id, invitee.id)
    return _group_public(request, g)


@app.delete("/api/groups/{group_id}/invites/{user_id}")
async def cancel_group_invite(request: Request, group_id: str, user_id: str):
    g = _group_call(_groups(request).cancel_invite, group_id, U(request).id, user_id)
    return _group_public(request, g)


@app.post("/api/groups/{group_id}/respond")
async def respond_to_invite(request: Request, group_id: str, body: dict[str, Any] = Body(...)):
    g = _group_call(_groups(request).respond, group_id, U(request).id, bool(body.get("accept")))
    return _group_public(request, g)


@app.delete("/api/groups/{group_id}/members/{user_id}")
async def remove_group_member(request: Request, group_id: str, user_id: str):
    g = _group_call(_groups(request).remove_member, group_id, U(request).id, user_id)
    return _group_public(request, g) if g.is_member(U(request).id) else {"ok": True}


# -- settings & models -------------------------------------------------------


@app.get("/api/settings")
async def get_settings(request: Request):
    return S(request).public_settings(C(request))


@app.put("/api/settings")
async def put_settings(request: Request, body: dict[str, Any] = Body(...)):
    try:
        await S(request).save_settings(C(request), body)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    return await get_settings(request)


@app.post("/api/models")
async def list_models(request: Request, body: dict[str, Any] | None = Body(None)):
    """Models at the configured endpoint, or (admins) at the one in ``body`` — Test Connection."""
    s = S(request)
    overrides = {k: v for k, v in (body or {}).items() if k in ("base_url", "api_key") and v}
    if overrides and not U(request).is_admin:
        raise HTTPException(403, "only an admin can test other connections")
    settings = Settings.from_dict({**s.settings.to_dict(), **overrides})
    try:
        return {"models": await llm.fetch_models(settings)}
    except llm.LLMError as e:
        raise HTTPException(502, str(e)) from e


# -- conversations -------------------------------------------------------------


def _ctx_in(request: Request, group_id: str | None) -> UserCtx:
    """The user's context, moved into ``group_id`` when given (members only)."""
    ctx = C(request)
    if not group_id:
        return ctx
    return S(request).group_ctx(ctx, _member_group(request, group_id))


def _check_author(ctx: UserCtx, convo, owner_too: bool = False) -> None:
    """In a group, only whoever started a chat changes it (the group's owner may also delete it)."""
    if not ctx.group or convo.user_id == ctx.user.id:
        return
    if owner_too and ctx.group.is_owner(ctx.user.id):
        return
    who = convo.user_name or "the person who started it"
    raise HTTPException(403, f"only {who} can change this chat")


@app.get("/api/conversations")
async def conversations(request: Request, group_id: str | None = None):
    ctx = _ctx_in(request, group_id)
    convos = sorted(config.load_conversations(ctx.convos_dir), key=lambda c: c.updated_at, reverse=True)
    return [c.summary() for c in convos]


def _convo_or_404(ctx: UserCtx, convo_id: str):
    try:
        c = config.load_conversation(convo_id, ctx.convos_dir)
    except ValueError:
        c = None
    if not c:
        raise HTTPException(404, "conversation not found")
    return c


@app.get("/api/conversations/{convo_id}")
async def get_conversation(request: Request, convo_id: str, group_id: str | None = None):
    return _convo_or_404(_ctx_in(request, group_id), convo_id).to_dict()


@app.patch("/api/conversations/{convo_id}")
async def patch_conversation(request: Request, convo_id: str, body: dict[str, Any] = Body(...),
                             group_id: str | None = None):
    ctx = _ctx_in(request, group_id)
    c = _convo_or_404(ctx, convo_id)
    _check_author(ctx, c)
    if "title" in body and str(body["title"]).strip():
        c.title = str(body["title"]).strip()[:200]
    for key in ("model", "system_prompt"):
        if key in body:
            setattr(c, key, str(body[key] or ""))
    c.updated_at = now_iso()
    config.save_conversation(c, ctx.convos_dir)
    return c.summary()


@app.delete("/api/conversations/{convo_id}")
async def delete_conversation(request: Request, convo_id: str, group_id: str | None = None):
    ctx = _ctx_in(request, group_id)
    _check_author(ctx, _convo_or_404(ctx, convo_id), owner_too=True)
    config.delete_conversation(convo_id, ctx.convos_dir)
    return {"ok": True}


@app.get("/api/conversations/{convo_id}/export")
async def export_conversation(request: Request, convo_id: str, format: str = "md", group_id: str | None = None):
    c = _convo_or_404(_ctx_in(request, group_id), convo_id)
    safe = "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in c.title).strip() or "conversation"
    if format == "json":
        return JSONResponse(c.to_dict(), headers={"Content-Disposition": f'attachment; filename="{safe}.json"'})
    lines = [f"# {c.title}\n"]
    for m in c.messages:
        lines.append(f"## {'You' if m.role == 'user' else 'Assistant'}\n")
        for t in m.tool_calls:
            lines.append(f"> Tool `{t.server}/{t.tool}` ({t.status})\n")
        lines.append(f"{m.content}\n")
    return PlainTextResponse("\n".join(lines), media_type="text/markdown",
                             headers={"Content-Disposition": f'attachment; filename="{safe}.md"'})


# -- chat ------------------------------------------------------------------------


class AttachmentIn(BaseModel):
    name: str = Field("file", max_length=255)
    kind: Literal["image", "document"] | None = None
    data: str | None = None  # data:image/...;base64,... for images
    text: str | None = Field(None, max_length=MAX_DOC_CHARS)


class ChatRequest(BaseModel):
    conversation_id: str | None = Field(None, max_length=64)
    content: str = Field("", max_length=100_000)
    attachments: list[AttachmentIn] = Field(default_factory=list, max_length=20)
    model: str | None = Field(None, max_length=200)
    regenerate: bool = False
    edit_message_id: str | None = Field(None, max_length=64)
    group_id: str | None = Field(None, max_length=64)


@app.post("/api/chat")
async def post_chat(request: Request, req: ChatRequest):
    s = S(request)
    ctx = _ctx_in(request, req.group_id)
    body = req.model_dump()
    cid = body.get("conversation_id")
    if cid and any(st.conversation_id == cid and st.user_id == ctx.user.id and st.task and not st.task.done()
                   for st in s.streams.values()):
        raise HTTPException(409, "this conversation is already generating a reply")
    if cid and ctx.group:
        _check_author(ctx, _convo_or_404(ctx, cid))
    try:
        convo, user_msg = chat.prepare_conversation(s, ctx, body)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    stream = chat.Stream(new_id(), convo.id, ctx.user.id, ctx.group.id if ctx.group else "")
    s.streams[stream.id] = stream
    stream.emit({"type": "start", "stream_id": stream.id, "conversation": convo.summary(),
                 "user_message": user_msg.to_dict() if user_msg else None})

    async def produce():
        try:
            await chat.run_reply(s, ctx, stream, convo)
        finally:
            s.streams.pop(stream.id, None)

    stream.task = asyncio.create_task(produce())
    return StreamingResponse(chat.relay(stream), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _own_stream(request: Request, stream_id: str) -> chat.Stream | None:
    st = S(request).streams.get(stream_id)
    return st if st and st.user_id == U(request).id else None


@app.post("/api/streams/{stream_id}/stop")
async def stop_stream(request: Request, stream_id: str):
    st = _own_stream(request, stream_id)
    if st and st.task and not st.task.done():
        st.task.cancel()
    return {"ok": True}


@app.post("/api/streams/{stream_id}/approve")
async def approve_tool(request: Request, stream_id: str, body: dict[str, Any] = Body(...)):
    st = _own_stream(request, stream_id)
    fut = st.approvals.get(body.get("call_id", "")) if st else None
    if not fut or fut.done():
        raise HTTPException(404, "no tool call is waiting for approval")
    fut.set_result({"approved": bool(body.get("approved")), "always": bool(body.get("always"))})
    return {"ok": True}


@app.post("/api/attachments")
async def attachments(files: list[UploadFile] = File(...)):
    out = []
    for f in files:
        name = Path(f.filename or "file").name
        data = await f.read(MAX_ATTACHMENT_BYTES + 1)
        if len(data) > MAX_ATTACHMENT_BYTES:
            out.append({"name": name, "error": "file is larger than 25 MB"})
        elif parsers.is_image(name):
            mime = f.content_type if (f.content_type or "").startswith("image/") else "image/jpeg"
            out.append({"name": name, "kind": "image", "size": len(data),
                        "data": f"data:{mime};base64,{base64.b64encode(data).decode()}"})
        elif parsers.is_document(name):
            text, error = await asyncio.to_thread(_extract_bytes, name, data)
            if error:
                out.append({"name": name, "error": error})
            else:
                out.append({"name": name, "kind": "document", "size": len(data), "text": text[:MAX_DOC_CHARS]})
        else:
            out.append({"name": name, "error": "unsupported file type"})
    return out


def _extract_bytes(name: str, data: bytes) -> tuple[str, str]:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / name
        p.write_bytes(data)
        try:
            text = parsers.extract_document_text(p)
        except Exception as e:  # noqa: BLE001
            return "", f"could not read this document ({e})"
    return (text, "") if text.strip() else ("", "no text found in this document")


# -- memory (per user) ------------------------------------------------------------------


@app.get("/api/memory")
async def get_memory(request: Request):
    return [e.__dict__ for e in C(request).memory.list()]


@app.post("/api/memory")
async def add_memory(request: Request, body: dict[str, Any] = Body(...)):
    e = C(request).memory.add(str(body.get("text", "")))
    if not e:
        raise HTTPException(400, "empty, or already remembered")
    return e.__dict__


@app.delete("/api/memory/{entry_id}")
async def delete_memory(request: Request, entry_id: str):
    C(request).memory.remove(entry_id)
    return {"ok": True}


@app.delete("/api/memory")
async def clear_memory(request: Request):
    C(request).memory.clear()
    return {"ok": True}


# -- skills (shared; admins manage) -------------------------------------------------------


@app.get("/api/skills")
async def get_skills(request: Request):
    store = S(request).skills
    store.reload()
    return [s.to_dict() for s in store.skills]


@app.get("/api/skills/{name}")
async def get_skill(request: Request, name: str):
    s = S(request).skills.get(name)
    if not s:
        raise HTTPException(404, "skill not found")
    return s.to_dict(with_instructions=True)


@app.post("/api/skills")
async def save_skill(request: Request, body: dict[str, Any] = Body(...)):
    admin(request)
    store = S(request).skills
    try:
        original = body.get("original_name")
        name = store.create(body.get("name", ""), body.get("description", ""), body.get("instructions", ""),
                            overwrite=bool(body.get("overwrite")) or original == body.get("name"))
        if original and original != name:
            store.remove(original)
    except SkillExists as e:
        raise HTTPException(409, str(e)) from e
    except SkillError as e:
        raise HTTPException(400, str(e)) from e
    return {"name": name}


@app.post("/api/skills/install")
async def install_skill(request: Request, file: UploadFile = File(...), overwrite: bool = Form(False)):
    admin(request)
    store = S(request).skills
    name = Path(file.filename or "SKILL.md").name
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / name
        p.write_bytes(await file.read())
        try:
            return {"name": store.install_path(p, overwrite)}
        except SkillExists as e:
            raise HTTPException(409, str(e)) from e
        except (SkillError, OSError, UnicodeDecodeError) as e:
            raise HTTPException(400, str(e)) from e


@app.delete("/api/skills/{name}")
async def delete_skill(request: Request, name: str):
    admin(request)
    S(request).skills.remove(name)
    return {"ok": True}


# -- knowledge base ------------------------------------------------------------------------------
# scope "team": the shared knowledge folder (everyone can read; admins manage).
# scope "mine": the signed-in user's own documents (server mode only; they manage them).
# scope "group" + group_id: a group's documents (server mode only; every member manages them).
# Local mode has a single knowledge base, so "mine" and "team" mean the same one there.

MAX_PERSONAL_KB_BYTES = 200 * 1024 * 1024


def _kb(request: Request, scope: str, write: bool, group_id: str | None = None):
    """(knowledge base, True if the user manages it themselves: their own or their group's)."""
    s = S(request)
    if scope == "group":
        return s.group_kb(_member_group(request, group_id or "").id), True
    if scope == "mine" and s.is_server:
        return C(request).kb, True
    if scope not in ("mine", "team"):
        raise HTTPException(400, "scope must be 'mine', 'team' or 'group'")
    if write:
        admin(request)
    return s.kb, False


@app.get("/api/knowledge")
async def knowledge(request: Request, scope: str = "team", group_id: str | None = None):
    s, u = S(request), U(request)
    kb, personal = _kb(request, scope, write=False, group_id=group_id)
    linked = personal and kb.no_links
    group = s.groups.get(group_id) if scope == "group" else None
    return {
        "scope": "group" if scope == "group" else "mine" if personal else "team",
        "personal_available": s.is_server,
        "can_manage": personal or u.is_admin,
        # A linked shared folder: the folder is the source of truth, so files are added or removed there.
        "linked": linked,
        "linked_folder": str(kb.folder) if linked else "",
        "can_link": personal and bool(s.knowledge_roots()) and (group is None or group.is_owner(u.id)),
        "limit_reached": kb.limit_reached,
        "folder": str(kb.folder) if (u.is_admin and not personal) else "",
        "folder_exists": kb.folder.is_dir(),
        "embedding_method": kb.embedding_method,
        "syncing": kb.syncing,
        "progress": kb.progress,
        "last_sync": "" if personal else s.last_sync,
        "files": [{**f.__dict__, "filename": f.filename} for f in kb.list_files()],
    }


@app.post("/api/knowledge/upload")
async def knowledge_upload(request: Request, files: list[UploadFile] = File(...), scope: str = Form("team"),
                           group_id: str | None = Form(None)):
    s = S(request)
    kb, personal = _kb(request, scope, write=True, group_id=group_id)
    _not_linked(kb)
    used = kb.folder_bytes() if personal else 0
    errors = []
    for f in files:
        data = await f.read(MAX_ATTACHMENT_BYTES + 1)
        name = Path(f.filename or "file").name
        if len(data) > MAX_ATTACHMENT_BYTES:
            errors.append(f"{name}: larger than 25 MB")
            continue
        if personal and used + len(data) > MAX_PERSONAL_KB_BYTES:
            whose = "the group's" if scope == "group" else "your"
            errors.append(f"{name}: {whose} documents are limited to 200 MB in total")
            continue
        try:
            kb.import_bytes(name, data)
            used += len(data)
        except (ValueError, OSError) as e:
            errors.append(str(e))
    s.spawn(kb.sync(C(request).settings if personal else s.settings))
    return {"errors": errors}


@app.post("/api/knowledge/sync")
async def knowledge_sync(request: Request, body: dict[str, Any] | None = Body(None)):
    body = body or {}
    kb, _ = _kb(request, str(body.get("scope", "team")), write=True, group_id=body.get("group_id"))
    if body.get("reindex"):
        kb.mark_for_reindex(body.get("file_id"))
    result = await kb.sync(S(request).settings)
    return {"summary": result.summary() or "Up to date"}


@app.delete("/api/knowledge/files/{file_id}")
async def knowledge_delete(request: Request, file_id: str, scope: str = "team", group_id: str | None = None):
    kb, _ = _kb(request, scope, write=True, group_id=group_id)
    _not_linked(kb)
    kb.remove_file(file_id)
    await kb.sync(S(request).settings)
    return {"ok": True}


def _not_linked(kb) -> None:
    if kb.no_links:
        raise HTTPException(400, "This knowledge base mirrors a shared folder. Add or remove files in that folder; "
                                 "changes are picked up automatically.")


@app.get("/api/knowledge/browse")
async def knowledge_browse(request: Request, path: str = ""):
    """Subfolders a user may link (server mode): the allowed roots, or the folders inside ``path``."""
    s = S(request)
    if not s.is_server:
        raise HTTPException(400, "in local mode, choose the knowledge folder in Settings")
    try:
        return await asyncio.to_thread(s.browse, path)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/knowledge/link")
async def knowledge_link(request: Request, body: dict[str, Any] = Body(...)):
    """Use a shared folder (and all its subfolders) as the user's own or a group's knowledge base.

    ``folder`` "" goes back to the documents uploaded in the app. For a group, only its owner may do this.
    """
    s = S(request)
    scope, group_id = str(body.get("scope", "mine")), str(body.get("group_id") or "")
    if not s.is_server or scope not in ("mine", "group"):
        raise HTTPException(400, "only your own or a group's knowledge base can be linked to a shared folder")
    if scope == "group":
        _member_group(request, group_id)
    try:
        await s.link_folder(str(body.get("folder") or ""), U(request), group_id if scope == "group" else "")
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except GroupError as e:
        raise HTTPException(403, str(e)) from e
    return await knowledge(request, scope, group_id or None)


@app.post("/api/knowledge/open-folder")
async def knowledge_open(request: Request):
    local_only(request)
    folder = S(request).kb.folder
    folder.mkdir(parents=True, exist_ok=True)
    _open_path(folder)
    return {"ok": True}


def _open_path(p: Path) -> None:
    if sys.platform == "win32":
        os.startfile(p)  # noqa: S606
    else:
        import subprocess

        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(p)])


@app.post("/api/system/pick-folder")
async def pick_folder(request: Request, body: dict[str, Any] | None = Body(None)):
    """Native folder picker (the browser can't return real folder paths). Local mode only."""
    local_only(request)

    def pick() -> str:
        import tkinter
        from tkinter import filedialog

        root = tkinter.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        try:
            return filedialog.askdirectory(initialdir=(body or {}).get("initial") or None,
                                           title="Choose a folder") or ""
        finally:
            root.destroy()
    try:
        return {"path": await asyncio.to_thread(pick)}
    except Exception as e:  # noqa: BLE001 - e.g. no tkinter / no display
        raise HTTPException(501, f"folder picker unavailable ({e}); type the path instead") from e


# -- MCP (shared; admins manage) ---------------------------------------------------------------


def _mcp_public(server: dict[str, Any], is_admin: bool) -> dict[str, Any]:
    if is_admin:
        return server
    # Commands, env vars and headers may contain tokens: users only see what tools exist.
    cfg = server["config"]
    return {**server, "log_tail": "", "error": "unavailable" if server["error"] else "",
            "config": {"type": cfg.get("type"), "disabled": cfg.get("disabled", False)}}


@app.get("/api/mcp")
async def mcp_list(request: Request):
    u = U(request)
    servers = [_mcp_public(x, u.is_admin) for x in S(request).mcp.list()]
    return {"servers": servers, "config_path": str(config.MCP_FILE) if u.is_admin else ""}


@app.put("/api/mcp/servers/{name}")
async def mcp_upsert(request: Request, name: str, body: dict[str, Any] = Body(...)):
    admin(request)
    try:
        rt = await S(request).mcp.upsert(name, body.get("config") or {}, old_name=body.get("old_name"))
    except MCPConfigError as e:
        raise HTTPException(400, str(e)) from e
    return rt.to_dict()


@app.post("/api/mcp/import")
async def mcp_import(request: Request, body: dict[str, Any] = Body(...)):
    admin(request)
    try:
        return {"added": await S(request).mcp.import_json(body.get("json", ""))}
    except MCPConfigError as e:
        raise HTTPException(400, str(e)) from e


@app.delete("/api/mcp/servers/{name}")
async def mcp_delete(request: Request, name: str):
    admin(request)
    await S(request).mcp.remove(name)
    return {"ok": True}


@app.post("/api/mcp/servers/{name}/restart")
async def mcp_restart(request: Request, name: str):
    admin(request)
    rt = await S(request).mcp.restart(name)
    if not rt:
        raise HTTPException(404, "server not found")
    return rt.to_dict()


@app.post("/api/mcp/servers/{name}/enabled")
async def mcp_enabled(request: Request, name: str, body: dict[str, Any] = Body(...)):
    admin(request)
    rt = await S(request).mcp.set_enabled(name, bool(body.get("enabled")))
    if not rt:
        raise HTTPException(404, "server not found")
    return rt.to_dict()


@app.patch("/api/mcp/servers/{name}/tools/{tool}")
async def mcp_tool(request: Request, name: str, tool: str, body: dict[str, Any] = Body(...)):
    admin(request)
    rt = S(request).mcp.set_tool_flags(name, tool, body.get("enabled"), body.get("auto_approve"))
    if not rt:
        raise HTTPException(404, "server not found")
    return rt.to_dict()


# -- frontend ----------------------------------------------------------------------------


@app.get("/{path:path}", include_in_schema=False)
async def frontend(path: str):
    if path.startswith("api/"):
        raise HTTPException(404)
    index = FRONTEND_DIST / "index.html"
    if not index.exists():
        return PlainTextResponse(
            "Frontend not built. Run `npm install && npm run build` in the frontend folder, "
            "or use the Vite dev server (npm run dev).", status_code=503)
    target = (FRONTEND_DIST / path).resolve()
    if path and target.is_file() and target.is_relative_to(FRONTEND_DIST.resolve()):
        return FileResponse(target)
    return FileResponse(index)
