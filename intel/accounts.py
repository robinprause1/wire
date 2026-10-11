"""Who may open the desk and who may run Steuerung.

Registration creates a pending member. An admin sets them active, and only then
does the desk show them the answers. The first account, or the one named in the
environment, is the admin.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from intel.store import Store, now_iso

COOKIE = "wire_session"
SESSION_DAYS = 14
_ROUNDS = 200_000
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_STATUSES = ("pending", "active", "blocked")


def public(user: dict[str, object]) -> dict[str, object]:
    return {key: user[key] for key in ("id", "email", "name", "role", "status", "createdAt")}


def needs_setup(store: Store) -> bool:
    return store.user_count() == 0


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), _ROUNDS).hex()
    return f"pbkdf2${_ROUNDS}${salt}${digest}"


def check_password(password: str, stored: object) -> bool:
    try:
        kind, rounds, salt, digest = str(stored).split("$")
        if kind != "pbkdf2":
            return False
        got = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds)).hex()
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, digest)


def _clean(name: object, email: object, password: object) -> tuple[str, str, str]:
    cleaned_name = str(name or "").strip()
    cleaned_email = str(email or "").strip().lower()
    cleaned_password = str(password or "")
    if len(cleaned_name) < 2 or len(cleaned_name) > 80:
        raise ValueError("The name needs 2 to 80 characters.")
    if not _EMAIL.match(cleaned_email):
        raise ValueError("The email does not look valid.")
    if len(cleaned_password) < 8 or len(cleaned_password) > 200:
        raise ValueError("The password needs at least 8 characters.")
    return cleaned_name, cleaned_email, cleaned_password


def register(store: Store, name: object, email: object, password: object) -> dict[str, object]:
    cleaned_name, cleaned_email, cleaned_password = _clean(name, email, password)
    if store.user_by_email(cleaned_email) is not None:
        raise ValueError("This email is already registered.")
    user = {
        "id": secrets.token_hex(8),
        "email": cleaned_email,
        "name": cleaned_name,
        "passwordHash": hash_password(cleaned_password),
        "role": "member",
        "status": "pending",
        "createdAt": now_iso(),
    }
    store.insert_user(user)
    return public(user)


def setup_admin(store: Store, name: object, email: object, password: object) -> dict[str, object]:
    if not needs_setup(store):
        raise ValueError("An admin already exists.")
    created = register(store, name, email, password)
    store.set_user(str(created["id"]), "admin", "active")
    found = store.user_by_id(str(created["id"]))
    if found is None:
        raise ValueError("The admin could not be created.")
    return public(found)


def ensure_admin(store: Store) -> None:
    """Optional bootstrap from the environment, only while nobody is registered."""
    email = os.environ.get("WIRE_ADMIN_EMAIL", "").strip().lower()
    password = os.environ.get("WIRE_ADMIN_PASSWORD", "")
    name = os.environ.get("WIRE_ADMIN_NAME", "").strip() or "Admin"
    if not email or not password or not needs_setup(store):
        return
    setup_admin(store, name, email, password)


def open_session(store: Store, email: object, password: object) -> tuple[dict[str, object], str]:
    found = store.user_by_email(str(email or "").strip().lower())
    if found is None or not check_password(str(password or ""), found["passwordHash"]):
        raise ValueError("Email or password is wrong.")
    if found["status"] == "blocked":
        raise ValueError("This account is blocked.")
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(UTC) + timedelta(days=SESSION_DAYS)).isoformat()
    store.put_session(hashlib.sha256(token.encode()).hexdigest(), str(found["id"]), expires)
    return public(found), token


def user_from_token(store: Store, token: str) -> dict[str, object] | None:
    if not token:
        return None
    found = store.session_user(hashlib.sha256(token.encode()).hexdigest())
    return None if found is None else public(found)


def close_session(store: Store, token: str) -> None:
    if token:
        store.delete_session(hashlib.sha256(token.encode()).hexdigest())


def list_users(store: Store) -> list[dict[str, object]]:
    return [public(user) for user in store.users()]


def set_status(store: Store, actor_id: str, user_id: object, status: object) -> dict[str, object]:
    cleaned = str(status or "")
    if cleaned not in _STATUSES:
        raise ValueError("Unknown status.")
    target_id = str(user_id or "")
    found = store.user_by_id(target_id)
    if found is None:
        raise LookupError("There is no such user.")
    if target_id == actor_id and cleaned != "active":
        raise ValueError("Your own account stays active.")
    if found["role"] == "admin" and cleaned != "active":
        raise ValueError("Admins stay active.")
    store.set_user(target_id, str(found["role"]), cleaned)
    if cleaned == "blocked":
        store.delete_user_sessions(target_id)
    updated = store.user_by_id(target_id)
    if updated is None:
        raise LookupError("There is no such user.")
    return public(updated)


def remove_user(store: Store, actor_id: str, user_id: object) -> None:
    target_id = str(user_id or "")
    found = store.user_by_id(target_id)
    if found is None:
        raise LookupError("There is no such user.")
    if target_id == actor_id or found["role"] == "admin":
        raise ValueError("Admins cannot be removed here.")
    store.delete_user(target_id)


def cookie_header(token: str) -> str:
    if not token:
        return f"{COOKIE}=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0"
    return f"{COOKIE}={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age={SESSION_DAYS * 24 * 3600}"


def read_cookie(header: str) -> str:
    for part in (header or "").split(";"):
        name, _, value = part.strip().partition("=")
        if name == COOKIE:
            return value
    return ""


def safe_next(raw: str) -> str:
    path = raw.split("#", 1)[0]
    if path.startswith("/") and not path.startswith("//") and "\\" not in path:
        return quote(path, safe="/")
    return "/"


def clearance(user: dict[str, object] | None, method: str, path: str) -> str:
    """ok, login, pending or forbidden. Static files and the login itself stay open."""
    if path in ("/login", "/favicon.ico") or path.startswith("/static/"):
        return "ok"
    if path in ("/api/me", "/api/session", "/api/register", "/api/setup"):
        return "ok"
    if user is None or user.get("status") == "blocked":
        return "login"
    if user.get("status") != "active":
        return "pending"
    if _admin_only(method, path) and user.get("role") != "admin":
        return "forbidden"
    return "ok"


def _admin_only(method: str, path: str) -> bool:
    if path == "/manage" or path.startswith("/api/users") or path.startswith("/api/settings") or path == "/api/options":
        return True
    if method != "GET" and path.startswith("/api/desks") and not path.endswith("/ask"):
        return True
    return False
