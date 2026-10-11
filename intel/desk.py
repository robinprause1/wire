"""A desk is one configured pipeline: sources, steps, a schedule, and the question Jev answers.

Everything a user can change lives here as plain data. Nothing in this module
talks to the network or the database; `parse` is the single place where
untrusted input becomes a Desk, and it raises ValueError with a message meant
for the panel.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from intel.connectors import REGISTRY, connector

STEP_IDS = ("fetch", "ingest", "retrieve", "judge")
STEPS: tuple[tuple[str, str, str], ...] = (
    ("fetch", "Fetch sources", "Pulls every enabled source of this desk."),
    ("ingest", "Write index", "Clean, embed, then BM25 and dense vectors into Qdrant."),
    ("retrieve", "Pick candidates", "Finds the subjects inside the window that Jev gets asked about."),
    ("judge", "Ask Jev", "One yes/no question per candidate. Unchanged notes skip the call."),
)
PICKER_IDS = ("coins", "watchlist", "corpus")
SCHEDULE_MODES = ("hourly", "interval")
SUBJECT_TOKEN = "{subject}"
_SLUG = re.compile(r"[^a-z0-9]+")
_MIN_MINUTES = 15
_MAX_MINUTES = 1440


def slug(value: str, fallback: str = "desk") -> str:
    cleaned = _SLUG.sub("-", value.lower()).strip("-")
    return cleaned[:40] or fallback


@dataclass(frozen=True)
class SourceSpec:
    id: str
    kind: str
    name: str
    params: Mapping[str, str] = field(default_factory=dict)
    enabled: bool = True

    @property
    def cost(self) -> str:
        return connector(self.kind).cost


@dataclass(frozen=True)
class Schedule:
    enabled: bool = True
    mode: str = "hourly"
    every_minutes: int = 60

    def next_run_iso(self, last_cycle_at: str | None, now: datetime | None = None) -> str:
        current = now or datetime.now().astimezone()
        if not self.enabled:
            return ""
        if self.mode == "hourly":
            upcoming = current.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            return upcoming.isoformat()
        every = timedelta(minutes=self.every_minutes)
        if last_cycle_at:
            try:
                last = datetime.fromisoformat(last_cycle_at)
            except ValueError:
                last = current
            if last.tzinfo is None:
                last = last.replace(tzinfo=UTC)
            due = last.astimezone(current.tzinfo) + every
            if due > current:
                return due.isoformat()
        return (current + every).isoformat()

    def describe(self) -> str:
        if not self.enabled:
            return "paused"
        if self.mode == "interval":
            return f"every {self.every_minutes} min"
        return "hourly"


@dataclass(frozen=True)
class Brief:
    """What Jev is asked, about which candidates, on which evidence."""

    picker: str = "corpus"
    watchlist: tuple[str, ...] = ()
    question: str = "Do the notes report a concrete, current development that deserves attention right now?"
    when_true: str = "The notes describe something new and concrete that happened or was announced."
    when_false: str = "The notes are routine, speculative, or say nothing concrete."
    window_hours: int = 36
    yes_at: float = 0.40

    def instructions(self, subject_label: str) -> str:
        return self.question.replace(SUBJECT_TOKEN, subject_label)


@dataclass(frozen=True)
class Desk:
    id: str
    name: str
    summary: str = ""
    sources: tuple[SourceSpec, ...] = ()
    steps: Mapping[str, bool] = field(default_factory=lambda: {step: True for step in STEP_IDS})
    schedule: Schedule = field(default_factory=Schedule)
    brief: Brief = field(default_factory=Brief)

    def enabled(self, step: str) -> bool:
        return bool(self.steps.get(step, True))

    def to_json(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "summary": self.summary,
            "sources": [
                {"id": item.id, "kind": item.kind, "name": item.name, "params": dict(item.params), "enabled": item.enabled, "cost": item.cost}
                for item in self.sources
            ],
            "steps": {step: self.enabled(step) for step in STEP_IDS},
            "schedule": {
                "enabled": self.schedule.enabled,
                "mode": self.schedule.mode,
                "everyMinutes": self.schedule.every_minutes,
            },
            "brief": {
                "picker": self.brief.picker,
                "watchlist": list(self.brief.watchlist),
                "question": self.brief.question,
                "whenTrue": self.brief.when_true,
                "whenFalse": self.brief.when_false,
                "windowHours": self.brief.window_hours,
                "yesAt": self.brief.yes_at,
            },
        }


def parse(data: object, *, desk_id: str) -> Desk:
    """Turn panel or stored JSON into a Desk. Raises ValueError with a message for the user."""
    if not isinstance(data, dict):
        raise ValueError("Expected an object.")
    name = _text(data.get("name"), 80)
    if not name:
        raise ValueError("The desk needs a name.")
    sources = _sources(data.get("sources"))
    steps_raw = data.get("steps")
    steps = {step: True for step in STEP_IDS}
    if isinstance(steps_raw, dict):
        for step in STEP_IDS:
            if step in steps_raw:
                steps[step] = bool(steps_raw[step])
    return Desk(
        id=desk_id,
        name=name,
        summary=_text(data.get("summary"), 240),
        sources=tuple(sources),
        steps=steps,
        schedule=_schedule(data.get("schedule")),
        brief=_brief(data.get("brief")),
    )


def _sources(raw: object) -> list[SourceSpec]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("Sources must be a list.")
    if len(raw) > 60:
        raise ValueError("At most 60 sources per desk.")
    specs: list[SourceSpec] = []
    taken: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError("Each source must be an object.")
        kind = str(entry.get("kind") or "")
        if kind not in REGISTRY:
            raise ValueError(f"Unknown source kind: {kind or '—'}")
        found = REGISTRY[kind]
        params_raw = entry.get("params") if isinstance(entry.get("params"), dict) else {}
        params: dict[str, str] = {}
        for item in found.fields:
            value = _text(params_raw.get(item.key), 500) or str(found.defaults.get(item.key, ""))
            if item.options and value not in {key for key, _label in item.options}:
                raise ValueError(f"{found.label}: “{item.label}” has no valid value.")
            if not value:
                raise ValueError(f"{found.label}: “{item.label}” is missing.")
            if item.key == "url" and not value.startswith(("http://", "https://")):
                raise ValueError(f"{found.label}: the URL must start with http:// or https://.")
            params[item.key] = value
        name = _text(entry.get("name"), 80) or _default_name(found.label, params)
        source_id = slug(str(entry.get("id") or name), kind)
        base = source_id
        counter = 2
        while source_id in taken:
            source_id = f"{base}-{counter}"
            counter += 1
        taken.add(source_id)
        specs.append(SourceSpec(id=source_id, kind=kind, name=name, params=params, enabled=entry.get("enabled") is not False))
    return specs


def _default_name(label: str, params: Mapping[str, str]) -> str:
    detail = params.get("url") or params.get("channel") or params.get("network") or params.get("list") or ""
    detail = re.sub(r"^https?://(www\.)?", "", detail).rstrip("/")
    return f"{label} · {detail}"[:80] if detail else label


def _schedule(raw: object) -> Schedule:
    if not isinstance(raw, dict):
        return Schedule()
    mode = raw.get("mode")
    minutes = raw.get("everyMinutes")
    if isinstance(minutes, bool) or not isinstance(minutes, (int, float)):
        minutes = 60
    return Schedule(
        enabled=raw.get("enabled") is not False,
        mode=str(mode) if mode in SCHEDULE_MODES else "hourly",
        every_minutes=min(_MAX_MINUTES, max(_MIN_MINUTES, int(minutes))),
    )


def _brief(raw: object) -> Brief:
    default = Brief()
    if not isinstance(raw, dict):
        return default
    picker = raw.get("picker")
    if picker is not None and picker not in PICKER_IDS:
        raise ValueError("Unknown candidate picker.")
    watchlist_raw = raw.get("watchlist")
    watchlist: list[str] = []
    if isinstance(watchlist_raw, str):
        watchlist_raw = re.split(r"[\n,;]+", watchlist_raw)
    if isinstance(watchlist_raw, list):
        for word in watchlist_raw:
            cleaned = _text(word, 60)
            if cleaned and cleaned.lower() not in {item.lower() for item in watchlist}:
                watchlist.append(cleaned)
    if picker == "watchlist" and not watchlist:
        raise ValueError("The watchlist needs at least one term.")
    question = _text(raw.get("question"), 600) or default.question
    yes_at = raw.get("yesAt", default.yes_at)
    if isinstance(yes_at, bool) or not isinstance(yes_at, (int, float)):
        yes_at = default.yes_at
    window = raw.get("windowHours", default.window_hours)
    if isinstance(window, bool) or not isinstance(window, (int, float)):
        window = default.window_hours
    return Brief(
        picker=str(picker) if picker else default.picker,
        watchlist=tuple(watchlist[:60]),
        question=question,
        when_true=_text(raw.get("whenTrue"), 400) or default.when_true,
        when_false=_text(raw.get("whenFalse"), 400) or default.when_false,
        window_hours=min(24 * 14, max(1, int(window))),
        yes_at=min(0.9, max(0.05, round(float(yes_at), 2))),
    )


def _text(value: object, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]
