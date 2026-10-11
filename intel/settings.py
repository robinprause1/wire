"""Instance-wide settings: API keys and the Jev model. Secrets never leave this process in clear text."""

from __future__ import annotations

import os

from intel.connectors import REGISTRY
from intel.judge import DEFAULT_MODEL, JEV_SECRET, MODEL_KEY, model_name
from intel.store import Store

SECRET_KEYS: tuple[str, ...] = tuple(dict.fromkeys([JEV_SECRET, *(item.secret for item in REGISTRY.values() if item.secret)]))
_ALLOWED = set(SECRET_KEYS) | {MODEL_KEY}


def install_secrets(store: Store) -> None:
    """Panel values override the environment. .env still fills anything not saved here."""
    for key, value in store.secret_map().items():
        if key in _ALLOWED and value:
            os.environ[key] = value


def view(store: Store) -> dict[str, object]:
    return {
        "credentials": [_credential(store, key) for key in SECRET_KEYS],
        "model": model_name(),
    }


def save_secret(store: Store, key: object, value: object) -> None:
    if key not in SECRET_KEYS or not isinstance(value, str) or "\n" in value or len(value) > 500:
        raise ValueError("Invalid key.")
    cleaned = value.strip()
    if cleaned:
        store.set_secret(str(key), cleaned)
        os.environ[str(key)] = cleaned
    else:
        store.delete_secret(str(key))
        os.environ.pop(str(key), None)


def save_model(store: Store, value: object) -> None:
    if not isinstance(value, str) or "\n" in value or len(value) > 80:
        raise ValueError("Invalid model.")
    cleaned = value.strip() or DEFAULT_MODEL
    store.set_secret(MODEL_KEY, cleaned)
    os.environ[MODEL_KEY] = cleaned


def _credential(store: Store, key: str) -> dict[str, object]:
    stored = store.secret_map().get(key, "")
    trimmed = (stored or os.environ.get(key, "")).strip()
    users = [item.label for item in REGISTRY.values() if item.secret == key]
    return {
        "key": key,
        "label": "Jev / TypeSafe" if key == JEV_SECRET else key.removesuffix("_API_KEY").replace("_", " ").title(),
        "usedBy": ["station “Ask Jev”"] if key == JEV_SECRET else [f"source “{name}”" for name in users],
        "set": bool(trimmed),
        "hint": _hint(trimmed),
    }


def _hint(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 4:
        return "••••"
    return f"••••{value[-4:]}"
