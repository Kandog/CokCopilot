"""Users, access keys and login sessions for server mode.

- Each user has a random access key (``cok_...``). Only its SHA-256 hash is
  stored, in ``users.json`` in the data dir; the key is shown once on creation.
- API clients send ``Authorization: Bearer <key>``. The web UI logs in once
  with the key and then uses an HttpOnly session cookie, signed with a secret
  kept in ``secret.key``. The cookie embeds a hash of the user's current key,
  so resetting a key logs that user out everywhere.
- In local mode (the default desktop app on 127.0.0.1) there is a single
  built-in admin user and no login.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from . import config
from .models import new_id, now_iso

KEY_PREFIX = "cok_"
SESSION_COOKIE = "cok_session"
SESSION_DAYS = 30
ROLES = ("admin", "user")
METHOD_KEY = "key"
METHOD_SSO = "sso"


class AuthError(Exception):
    pass


@dataclass
class User:
    id: str
    name: str
    role: str = "user"
    key_hash: str = ""
    key_hint: str = ""  # first characters of the key, to tell keys apart
    created_at: str = ""
    disabled: bool = False
    # Microsoft Entra ID (Azure AD) single sign-on: the work email / UPN the admin entered,
    # and the account's object id, bound on the first SSO sign-in (it never changes, unlike the email).
    email: str = ""
    sso_oid: str = ""

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def public(self) -> dict:
        return {"id": self.id, "name": self.name, "role": self.role, "key_hint": self.key_hint,
                "created_at": self.created_at, "disabled": self.disabled, "email": self.email,
                "sso_linked": bool(self.sso_oid)}


LOCAL_USER = User(id="local", name="You", role="admin")


def _clean_email(email: str | None) -> str:
    email = (email or "").strip().lower()
    if email and ("@" not in email or " " in email or len(email) > 254):
        raise AuthError(f"{email} doesn't look like an email address")
    return email


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def new_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


class UserStore:
    def __init__(self, path: Path | None = None, secret_path: Path | None = None):
        self.path = path or config.DATA_DIR / "users.json"
        self.secret_path = secret_path or config.DATA_DIR / "secret.key"
        self._lock = threading.Lock()
        self.users: list[User] = []
        self._mtime = -1.0
        self._reload()
        self._secret = self._load_secret()

    def _reload(self) -> None:
        """Re-read users.json if it changed (e.g. `cokcopilot users add` while the server runs)."""
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            mtime = 0.0
        if mtime == self._mtime:
            return
        self._mtime = mtime
        data = config.read_json(self.path) or {}
        users = []
        for u in data.get("users", []):
            try:
                users.append(User(**{k: u[k] for k in User.__dataclass_fields__ if k in u}))
            except TypeError:
                continue
        self.users = users

    def _load_secret(self) -> bytes:
        try:
            s = self.secret_path.read_bytes()
            if len(s) >= 32:
                return s
        except OSError:
            pass
        s = secrets.token_bytes(32)
        self.secret_path.parent.mkdir(parents=True, exist_ok=True)
        self.secret_path.write_bytes(s)
        return s

    def _save(self) -> None:
        config.write_json(self.path, {"users": [asdict(u) for u in self.users]})
        try:
            self._mtime = self.path.stat().st_mtime
        except OSError:
            pass

    # -- management --

    def list(self) -> list[User]:
        self._reload()
        return list(self.users)

    def get(self, user_id: str) -> User | None:
        self._reload()
        return next((u for u in self.users if u.id == user_id), None)

    def by_name(self, name: str) -> User | None:
        name = name.strip().lower()
        return next((u for u in self.users if u.name.lower() == name), None)

    def by_email(self, email: str) -> User | None:
        email = (email or "").strip().lower()
        return next((u for u in self.users if email and u.email == email), None)

    def by_oid(self, oid: str) -> User | None:
        return next((u for u in self.users if oid and u.sso_oid == oid), None)

    def add(self, name: str, role: str = "user", email: str = "") -> tuple[User, str]:
        name = " ".join(name.split())
        email = _clean_email(email)
        if not name:
            raise AuthError("give the user a name")
        if role not in ROLES:
            raise AuthError(f"role must be one of {', '.join(ROLES)}")
        with self._lock:
            self._reload()
            if self.by_name(name):
                raise AuthError(f'a user named "{name}" already exists')
            if email and self.by_email(email):
                raise AuthError(f"another user already has the email {email}")
            key = new_key()
            user = User(new_id(), name, role, hash_key(key), key[: len(KEY_PREFIX) + 4], now_iso(), email=email)
            self.users.append(user)
            self._save()
        return user, key

    def reset_key(self, user_id: str) -> str:
        with self._lock:
            u = self._require(user_id)
            key = new_key()
            u.key_hash, u.key_hint = hash_key(key), key[: len(KEY_PREFIX) + 4]
            self._save()
        return key

    def update(self, user_id: str, role: str | None = None, disabled: bool | None = None,
               name: str | None = None, email: str | None = None) -> User:
        with self._lock:
            u = self._require(user_id)
            new_role = role if role is not None else u.role
            new_disabled = disabled if disabled is not None else u.disabled
            if new_role not in ROLES:
                raise AuthError(f"role must be one of {', '.join(ROLES)}")
            if u.is_admin and (new_role != "admin" or new_disabled) and self._active_admins() == 1:
                raise AuthError("there must be at least one active admin")
            if name is not None and name.strip():
                other = self.by_name(name)
                if other and other.id != u.id:
                    raise AuthError(f'a user named "{name.strip()}" already exists')
                u.name = " ".join(name.split())
            if email is not None:
                email = _clean_email(email)
                other = self.by_email(email)
                if other and other.id != u.id:
                    raise AuthError(f"another user already has the email {email}")
                if email != u.email:
                    u.email, u.sso_oid = email, ""  # a different person: bind again on their next sign-in
            u.role, u.disabled = new_role, new_disabled
            self._save()
            return u

    def remove(self, user_id: str) -> None:
        with self._lock:
            u = self._require(user_id)
            if u.is_admin and not u.disabled and self._active_admins() == 1:
                raise AuthError("you can't remove the last admin")
            self.users = [x for x in self.users if x.id != user_id]
            self._save()

    def sso_user(self, oid: str, emails: list[str]) -> User | None:
        """The app user for a Microsoft account: bound by object id, else matched by email (then bound)."""
        with self._lock:
            self._reload()
            u = self.by_oid(oid)
            if u is None:
                u = next((x for e in emails if (x := self.by_email(e)) and not x.sso_oid), None)
                if u is None:
                    return None
                u.sso_oid = oid
                self._save()
            return None if u.disabled else u

    def _require(self, user_id: str) -> User:
        u = self.get(user_id)
        if not u:
            raise AuthError("user not found")
        return u

    def _active_admins(self) -> int:
        return sum(1 for u in self.users if u.is_admin and not u.disabled)

    # -- authentication --

    def authenticate_key(self, key: str) -> User | None:
        if not key or not key.startswith(KEY_PREFIX):
            return None
        self._reload()
        h = hash_key(key.strip())
        for u in self.users:
            if not u.disabled and hmac.compare_digest(u.key_hash, h):
                return u
        return None

    def make_session(self, user: User, method: str = METHOD_KEY) -> str:
        """Signed cookie value. ``method`` records how the user signed in (access key or SSO)."""
        expires = int(time.time()) + SESSION_DAYS * 86400
        payload = f"{user.id}.{expires}" if method == METHOD_KEY else f"{user.id}.{expires}.{method}"
        return f"{payload}.{self._sign(payload, user.key_hash)}"

    def check_session(self, token: str) -> tuple[User, str] | None:
        """(user, sign-in method) for a valid session cookie."""
        try:
            parts = token.split(".")
            if len(parts) == 3:  # sessions from before SSO existed: access key
                (user_id, expires, sig), method = parts, METHOD_KEY
            else:
                user_id, expires, method, sig = parts
            if method not in (METHOD_KEY, METHOD_SSO) or int(expires) < time.time():
                return None
        except (ValueError, AttributeError):
            return None
        u = self.get(user_id)
        if not u or u.disabled:
            return None
        payload = token.rsplit(".", 1)[0]
        if not hmac.compare_digest(sig, self._sign(payload, u.key_hash)):
            return None
        return u, method

    def sign_value(self, value: str) -> str:
        """``value`` plus a signature, for short-lived cookies (e.g. the SSO sign-in state)."""
        return f"{value}.{self._sign(value, 'value')}"

    def unsign_value(self, signed: str) -> str | None:
        value, _, sig = (signed or "").rpartition(".")
        return value if value and hmac.compare_digest(sig, self._sign(value, "value")) else None

    def _sign(self, payload: str, key_hash: str) -> str:
        mac = hmac.new(self._secret, f"{payload}.{key_hash}".encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(mac).decode().rstrip("=")
