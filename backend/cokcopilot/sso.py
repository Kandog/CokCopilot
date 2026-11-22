"""Single sign-on with Microsoft Entra ID (Azure AD) for server mode.

OpenID Connect authorization-code flow with PKCE, as a confidential client
(client id + secret registered in the company's tenant):

1. ``/api/auth/sso/start`` sends the browser to Microsoft with a random
   ``state``, ``nonce`` and PKCE challenge, remembered in a short-lived signed cookie.
2. Microsoft sends the browser back to ``/api/auth/sso/callback?code=...&state=...``.
3. The server redeems the code at the tenant's token endpoint (over TLS, with the
   client secret) and reads the ID token it gets back. Because the token comes
   straight from Microsoft's token endpoint, OIDC Core 3.1.3.7 allows TLS server
   validation in place of checking its signature; issuer, audience, tenant,
   expiry and nonce are still checked.
4. The Microsoft account is matched to an **existing** app user: by object id
   once linked, otherwise by the email the admin entered for that user (then linked).
   Nobody gets in just by having a company account — admins still add users in the app.

Settings live in ``sso.json`` in the data dir. The client secret is never sent to browsers.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import time
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from . import config

LOGIN_HOST = "https://login.microsoftonline.com"
STATE_COOKIE = "cok_sso"
STATE_MAX_AGE = 600  # seconds the user has to finish signing in at Microsoft
CALLBACK_PATH = "/api/auth/sso/callback"
SCOPES = "openid profile email"
_GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
CLOCK_SKEW = 300


class SSOError(Exception):
    pass


@dataclass
class SSOConfig:
    enabled: bool = False
    tenant_id: str = ""  # Directory (tenant) ID, a GUID
    client_id: str = ""  # Application (client) ID, a GUID
    client_secret: str = ""
    # Empty = <the address users open>/api/auth/sso/callback. Must match the app registration exactly.
    redirect_uri: str = ""
    # While SSO is on, may users (not admins) still sign in with access keys? Admins always can,
    # so a wrong Azure setting can't lock everyone out.
    allow_access_keys: bool = False
    button_label: str = "Sign in with Microsoft"

    def problems(self) -> list[str]:
        out = []
        if not _GUID.match(self.tenant_id):
            out.append("Tenant ID must be your directory's ID (a GUID), not 'common' or a domain name")
        if not _GUID.match(self.client_id):
            out.append("Client ID must be the app registration's Application (client) ID, a GUID")
        if not self.client_secret:
            out.append("Client secret is missing")
        if self.redirect_uri and not self.redirect_uri.startswith(("https://", "http://localhost",
                                                                    "http://127.0.0.1")):
            out.append("Redirect URI must use https://")
        return out

    @property
    def ready(self) -> bool:
        return self.enabled and not self.problems()

    def public(self) -> dict[str, Any]:
        d = asdict(self)
        d["client_secret"] = ""
        d["client_secret_set"] = bool(self.client_secret)
        d["problems"] = self.problems()
        return d


class SSOStore:
    def __init__(self, path: Path | None = None):
        self.path = path or config.DATA_DIR / "sso.json"
        self._mtime = -1.0
        self.config = SSOConfig()
        self.reload()

    def reload(self) -> SSOConfig:
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            mtime = 0.0
        if mtime != self._mtime:
            self._mtime = mtime
            data = config.read_json(self.path) or {}
            known = {f.name for f in fields(SSOConfig)}
            try:
                self.config = SSOConfig(**{k: v for k, v in data.items() if k in known})
            except TypeError:
                self.config = SSOConfig()
        return self.config

    def save(self, body: dict[str, Any]) -> SSOConfig:
        current = asdict(self.reload())
        defaults = SSOConfig()
        for f in fields(SSOConfig):
            if f.name not in body or body[f.name] is None:
                continue
            if f.name == "client_secret" and not body[f.name]:
                continue  # empty = keep the saved secret
            v = body[f.name]
            current[f.name] = bool(v) if isinstance(getattr(defaults, f.name), bool) else str(v).strip()
        cfg = SSOConfig(**current)
        if cfg.enabled and cfg.problems():
            raise SSOError("Can't turn on single sign-on yet: " + "; ".join(cfg.problems()))
        config.write_json(self.path, asdict(cfg))
        self._mtime = -1.0
        return self.reload()


# -- the flow ---------------------------------------------------------------------------------


def new_login() -> dict[str, str]:
    """Random values for one sign-in attempt."""
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return {"state": secrets.token_urlsafe(24), "nonce": secrets.token_urlsafe(24),
            "verifier": verifier, "challenge": challenge, "exp": str(int(time.time()) + STATE_MAX_AGE)}


def authorize_url(cfg: SSOConfig, redirect_uri: str, login: dict[str, str]) -> str:
    q = {
        "client_id": cfg.client_id,
        "response_type": "code",
        "response_mode": "query",
        "redirect_uri": redirect_uri,
        "scope": SCOPES,
        "state": login["state"],
        "nonce": login["nonce"],
        "code_challenge": login["challenge"],
        "code_challenge_method": "S256",
        "prompt": "select_account",
    }
    return f"{LOGIN_HOST}/{cfg.tenant_id}/oauth2/v2.0/authorize?{urlencode(q)}"


async def redeem(cfg: SSOConfig, code: str, redirect_uri: str, verifier: str, nonce: str) -> dict[str, Any]:
    """Exchange the code for tokens and return the checked ID-token claims."""
    data = {
        "client_id": cfg.client_id,
        "client_secret": cfg.client_secret,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": verifier,
        "scope": SCOPES,
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(f"{LOGIN_HOST}/{cfg.tenant_id}/oauth2/v2.0/token", data=data)
    except httpx.HTTPError as e:
        raise SSOError(f"couldn't reach Microsoft sign-in ({e or type(e).__name__})") from e
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if resp.status_code >= 400 or "id_token" not in body:
        raise SSOError(body.get("error_description", "").split("\r\n")[0] or f"token request failed ({resp.status_code})")
    claims = decode_payload(body["id_token"])
    check_claims(cfg, claims, nonce)
    return claims


def decode_payload(jwt: str) -> dict[str, Any]:
    try:
        payload = jwt.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError) as e:
        raise SSOError("Microsoft returned an unreadable ID token") from e
    if not isinstance(claims, dict):
        raise SSOError("Microsoft returned an unreadable ID token")
    return claims


def check_claims(cfg: SSOConfig, claims: dict[str, Any], nonce: str, now: float | None = None) -> None:
    now = time.time() if now is None else now
    tenant = cfg.tenant_id.lower()
    if str(claims.get("tid", "")).lower() != tenant:
        raise SSOError("this account belongs to a different organization")
    if claims.get("iss") != f"{LOGIN_HOST}/{tenant}/v2.0":
        raise SSOError("the ID token has an unexpected issuer")
    aud = claims.get("aud")
    if (aud if isinstance(aud, list) else [aud]).count(cfg.client_id) != 1:
        raise SSOError("the ID token was issued for a different app")
    try:
        if float(claims["exp"]) < now - CLOCK_SKEW or float(claims.get("nbf", 0)) > now + CLOCK_SKEW:
            raise SSOError("the ID token has expired; try again")
    except (KeyError, TypeError, ValueError) as e:
        raise SSOError("the ID token has no valid expiry") from e
    if not nonce or claims.get("nonce") != nonce:
        raise SSOError("the sign-in response doesn't match this sign-in attempt; try again")
    if not claims.get("oid"):
        raise SSOError("the ID token has no account id")


def claim_emails(claims: dict[str, Any]) -> list[str]:
    """Addresses that identify the account: UPN / sign-in name first, then the email claim."""
    out: list[str] = []
    for k in ("preferred_username", "upn", "email"):
        v = str(claims.get(k) or "").strip().lower()
        if "@" in v and v not in out:
            out.append(v)
    return out


async def check_tenant(tenant_id: str) -> str:
    """Fetch the tenant's OpenID configuration: a quick check that the tenant ID is right."""
    if not _GUID.match(tenant_id):
        raise SSOError("Tenant ID must be your directory's ID (a GUID)")
    url = f"{LOGIN_HOST}/{tenant_id}/v2.0/.well-known/openid-configuration"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
    except httpx.HTTPError as e:
        raise SSOError(f"couldn't reach {LOGIN_HOST} from the server ({e or type(e).__name__})") from e
    if resp.status_code >= 400:
        raise SSOError("Microsoft doesn't know this tenant ID")
    return str(resp.json().get("issuer", ""))
