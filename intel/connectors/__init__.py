"""Connector registry. A desk names a kind and passes params; this module runs it."""

from __future__ import annotations

from collections.abc import Mapping

from intel.connectors import crypto, feeds
from intel.connectors.base import Connector, Context, Field, Item, Pull
from intel.store import Store

REGISTRY: Mapping[str, Connector] = {item.kind: item for item in (*feeds.CONNECTORS, *crypto.CONNECTORS)}


def connector(kind: str) -> Connector:
    try:
        return REGISTRY[kind]
    except KeyError:
        raise ValueError(f"Unknown source kind: {kind}") from None


def pull(store: Store, desk_id: str, source_id: str, kind: str, params: Mapping[str, str]) -> Pull:
    found = connector(kind)
    merged = {**found.defaults, **params}
    missing = found.missing(merged)
    if missing:
        return Pull(items=[], note=f"missing {', '.join(missing)}")
    return found.fetch(Context(store, desk_id, source_id), merged)


def describe() -> list[dict[str, object]]:
    """Schema for the panel: what kinds exist and which fields each one needs."""
    return [
        {
            "kind": item.kind,
            "label": item.label,
            "summary": item.summary,
            "cost": item.cost,
            "group": item.group,
            "secret": item.secret,
            "fields": [
                {
                    "key": field.key,
                    "label": field.label,
                    "placeholder": field.placeholder,
                    "options": [{"value": value, "label": label} for value, label in field.options],
                }
                for field in item.fields
            ],
            "defaults": dict(item.defaults),
        }
        for item in REGISTRY.values()
    ]


__all__ = ["Connector", "Context", "Field", "Item", "Pull", "REGISTRY", "connector", "describe", "pull"]
