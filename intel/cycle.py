"""One cycle of one desk: fetch each source, write the index, ask Jev. One cycle runs at a time."""

from __future__ import annotations

import threading

from intel import connectors
from intel.desk import Desk, SourceSpec
from intel.index import WireIndex
from intel.judge import ask_jev
from intel.store import Store, now_iso
from intel.subjects import picker_for


def last_cycle_key(desk_id: str) -> str:
    return f"{desk_id}:last_cycle_at"


class Runner:
    """Starts cycles on a worker thread and refuses a second one while the first is busy."""

    def __init__(self, store: Store, index: WireIndex) -> None:
        self.store = store
        self.index = index
        self._lock = threading.Lock()
        self.running: str | None = None
        self.started_at: str | None = None

    def busy(self, desk_id: str | None = None) -> bool:
        return self.running is not None if desk_id is None else self.running == desk_id

    def start(self, desk: Desk, *, force_jev: bool = False, fetch: bool = True) -> bool:
        with self._lock:
            if self.running is not None:
                return False
            self.running = desk.id
            self.started_at = now_iso()
        threading.Thread(target=self._run, args=(desk, force_jev, fetch), daemon=True).start()
        return True

    def _run(self, desk: Desk, force_jev: bool, fetch: bool) -> None:
        try:
            if fetch:
                run_cycle(self.store, self.index, desk, force_jev=force_jev)
            else:
                ask_jev(self.store, self.index, desk, force=force_jev)
        finally:
            with self._lock:
                self.running = None
                self.started_at = None


def run_cycle(store: Store, index: WireIndex, desk: Desk, *, force_jev: bool) -> None:
    if desk.enabled("fetch"):
        _fetch_sources(store, index, desk)
    if force_jev or (desk.enabled("retrieve") and desk.enabled("judge")):
        ask_jev(store, index, desk, force=force_jev)
    store.set_state(last_cycle_key(desk.id), now_iso())


def _fetch_sources(store: Store, index: WireIndex, desk: Desk) -> None:
    known = store.known_ids()
    tagger = picker_for(desk, index).tags
    for source in desk.sources:
        started = now_iso()
        if not source.enabled:
            store.record_run(desk.id, source.id, started, "skipped", 0, 0, "ausgeschaltet")
            continue
        try:
            pull = connectors.pull(store, desk.id, source.id, source.kind, source.params)
        except Exception as error:
            store.record_run(desk.id, source.id, started, "error", 0, 0, f"{type(error).__name__}: {error}")
            continue
        if not desk.enabled("ingest"):
            store.record_run(desk.id, source.id, started, "skipped", len(pull.items), 0, "index writing is off")
            continue
        try:
            inserted, ids = index.ingest(desk.id, source.kind, pull.items, known, tagger)
        except Exception as error:
            store.record_run(desk.id, source.id, started, "error", len(pull.items), 0, f"{type(error).__name__}: {error}")
            continue
        known.update(ids)
        store.remember(ids)
        _record_pull(store, desk, source, started, pull, inserted)


def _record_pull(store: Store, desk: Desk, source: SourceSpec, started: str, pull: connectors.Pull, inserted: int) -> None:
    note = pull.note
    if pull.credits_cost is not None:
        note = f"{note} · {pull.credits_cost} credits".strip(" ·")
    skipped = note.startswith(("credit floor", "already pulled", "missing")) or " missing" in note
    store.record_run(desk.id, source.id, started, "skipped" if skipped else "ok", len(pull.items), inserted, note)
