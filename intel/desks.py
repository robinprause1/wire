"""The desks a Wire instance runs. Load, create from a template, replace, delete.

This is the only module that reads or writes desks in the store, so the server
and the clock share one view of what exists. Errors come back as ValueError
with a sentence for the panel.
"""

from __future__ import annotations

import json

from intel import desk as desk_model
from intel.desk import Desk, slug
from intel.store import LEGACY_DESK, Store
from intel.templates import template

_LEGACY_SETTINGS_KEY = "pipeline"
_MAX_DESKS = 24


def all_desks(store: Store) -> list[Desk]:
    found: list[Desk] = []
    for desk_id, body in store.desks().items():
        try:
            found.append(desk_model.parse(body, desk_id=desk_id))
        except ValueError:
            continue
    return found


def get(store: Store, desk_id: str) -> Desk:
    body = store.desks().get(desk_id)
    if body is None:
        raise LookupError(desk_id)
    return desk_model.parse(body, desk_id=desk_id)


def create(store: Store, template_id: str, name: str) -> Desk:
    existing = store.desks()
    if len(existing) >= _MAX_DESKS:
        raise ValueError(f"At most {_MAX_DESKS} desks.")
    chosen = template(template_id)
    title = " ".join(str(name or "").split())[:80] or chosen.name
    desk_id = _free_id(slug(title), set(existing))
    desk = chosen.build(desk_id, title)
    store.save_desk(desk.id, desk.to_json())
    return desk


def replace(store: Store, desk_id: str, payload: object) -> Desk:
    if desk_id not in store.desks():
        raise LookupError(desk_id)
    desk = desk_model.parse(payload, desk_id=desk_id)
    store.save_desk(desk.id, desk.to_json())
    return desk


def delete(store: Store, desk_id: str) -> None:
    if desk_id not in store.desks():
        raise LookupError(desk_id)
    store.delete_desk(desk_id)


def bootstrap(store: Store) -> list[Desk]:
    """First start: carry a pre-desk installation over to one crypto desk, or create it fresh."""
    desks = all_desks(store)
    if desks:
        return desks
    desk = template("crypto").build(LEGACY_DESK, "Crypto Desk")
    legacy_raw = store.get_state(_LEGACY_SETTINGS_KEY)
    if legacy_raw:
        desk = _apply_legacy(desk, legacy_raw)
        store.set_state(_LEGACY_SETTINGS_KEY, "")
    store.save_desk(desk.id, desk.to_json())
    return [desk]


def _apply_legacy(desk: Desk, raw: str) -> Desk:
    try:
        legacy = json.loads(raw)
    except json.JSONDecodeError:
        return desk
    if not isinstance(legacy, dict):
        return desk
    body = desk.to_json()
    disabled = legacy.get("disabledSources")
    if isinstance(disabled, list):
        for source in body["sources"]:
            if source["id"] in disabled:
                source["enabled"] = False
    if isinstance(legacy.get("steps"), dict):
        body["steps"] = {**body["steps"], **{key: bool(value) for key, value in legacy["steps"].items()}}
    body["schedule"] = {
        "enabled": legacy.get("scheduleEnabled") is not False,
        "mode": legacy.get("scheduleMode", "hourly"),
        "everyMinutes": legacy.get("everyMinutes", 60),
    }
    if "yesAt" in legacy:
        body["brief"]["yesAt"] = legacy["yesAt"]
    try:
        return desk_model.parse(body, desk_id=desk.id)
    except ValueError:
        return desk


def _free_id(base: str, taken: set[str]) -> str:
    candidate = base
    counter = 2
    while candidate in taken:
        candidate = f"{base}-{counter}"
        counter += 1
    return candidate
