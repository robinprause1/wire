"""What every connector produces and what it may touch.

A connector is a pure function from (context, params) to a Pull. The context
is the only door to persistent state, and it is scoped to one source inside one
desk, so two desks that read the same feed never share a cursor by accident.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from intel.store import Store


def shared_key(key: str) -> str:
    """Account-level state such as a credit balance. Same across desks and sources."""
    return f"shared:{key}"


@dataclass(frozen=True)
class Item:
    """One document as the connector saw it. The desk id is added at ingest."""

    source: str
    external_id: str
    title: str
    body: str
    url: str
    published_at: str


@dataclass(frozen=True)
class Pull:
    items: list[Item]
    credits_cost: int | None = None
    credits_remaining: int | None = None
    note: str = ""


@dataclass(frozen=True)
class Field:
    """One input the panel renders when a user adds a source of this kind."""

    key: str
    label: str
    placeholder: str = ""
    options: tuple[tuple[str, str], ...] = ()


class Context:
    """State door for one source in one desk."""

    def __init__(self, store: Store, desk_id: str, source_id: str) -> None:
        self.store = store
        self.desk_id = desk_id
        self.source_id = source_id

    def get(self, key: str) -> str | None:
        return self.store.get_state(self._scoped(key))

    def set(self, key: str, value: str) -> None:
        self.store.set_state(self._scoped(key), value)

    def shared_get(self, key: str) -> str | None:
        return self.store.get_state(shared_key(key))

    def shared_set(self, key: str, value: str) -> None:
        self.store.set_state(shared_key(key), value)

    def last_run(self) -> Mapping[str, object] | None:
        return self.store.last_runs(self.desk_id).get(self.source_id)

    @staticmethod
    def secret(name: str) -> str:
        return os.environ.get(name, "").strip()

    def _scoped(self, key: str) -> str:
        return f"{self.desk_id}:{self.source_id}:{key}"


@dataclass(frozen=True)
class Balance:
    """A metered account behind a connector, shown on the desk when the desk uses it."""

    label: str
    remaining: int | None
    per_pull: int
    floor: int


Fetch = Callable[[Context, Mapping[str, str]], Pull]
BalanceOf = Callable[[Store], Balance]


@dataclass(frozen=True)
class Connector:
    """A kind of source. The panel lists these; a desk instantiates them with params."""

    kind: str
    label: str
    summary: str
    fetch: Fetch
    cost: str = "free"
    fields: tuple[Field, ...] = ()
    secret: str = ""
    group: str = "Allgemein"
    defaults: Mapping[str, str] = field(default_factory=dict)
    balance: BalanceOf | None = None

    def missing(self, params: Mapping[str, str]) -> list[str]:
        return [item.key for item in self.fields if not str(params.get(item.key, "")).strip()]
