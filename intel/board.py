"""What the desk page and the panel show for one desk. Read-only; nothing here changes state."""

from __future__ import annotations

from intel.clock import next_run_at
from intel.connectors import connector
from intel.cycle import Runner, last_cycle_key
from intel.desk import Desk
from intel.index import WireIndex, embedder_name
from intel.store import Store, now_iso


def summary(store: Store, runner: Runner, desk: Desk) -> dict[str, object]:
    """One line per desk for switchers and lists."""
    return {
        "id": desk.id,
        "name": desk.name,
        "summary": desk.summary,
        "picker": desk.brief.picker,
        "sources": len(desk.sources),
        "schedule": desk.schedule.describe(),
        "running": runner.busy(desk.id),
        "nextRunAt": next_run_at(store, desk),
        "lastCycleAt": store.get_state(last_cycle_key(desk.id)) or "",
    }


def board(store: Store, index: WireIndex, runner: Runner, desk: Desk) -> dict[str, object]:
    runs = store.last_runs(desk.id)
    sources = []
    for source in desk.sources:
        run = runs.get(source.id)
        sources.append(
            {
                "id": source.id,
                "name": source.name,
                "kind": source.kind,
                "cost": source.cost,
                "enabled": source.enabled,
                "lastRunAt": None if run is None else run["finished_at"],
                "status": "idle" if run is None else run["status"],
                "fetched": 0 if run is None else int(run["fetched"]),
                "inserted": 0 if run is None else int(run["inserted"]),
                "note": "" if run is None else run["note"],
            }
        )
    return {
        "now": now_iso(),
        "desk": summary(store, runner, desk),
        "running": runner.busy(desk.id),
        "runningSince": runner.started_at if runner.busy(desk.id) else None,
        "busyElsewhere": runner.busy() and not runner.busy(desk.id),
        "nextRunAt": next_run_at(store, desk),
        "lastCycleAt": store.get_state(last_cycle_key(desk.id)) or "",
        "schedule": desk.to_json()["schedule"],
        "brief": desk.to_json()["brief"],
        "embedder": embedder_name(),
        "documents": index.count(desk.id),
        "balances": _balances(store, desk),
        "sources": sources,
        "items": index.latest(desk.id, 80),
        "verdicts": store.latest_batch(desk.id),
    }


def _balances(store: Store, desk: Desk) -> list[dict[str, object]]:
    """Metered accounts behind the desk's enabled sources, once per connector kind."""
    kinds = dict.fromkeys(source.kind for source in desk.sources if source.enabled)
    found = []
    for kind in kinds:
        balance_of = connector(kind).balance
        if balance_of is None:
            continue
        balance = balance_of(store)
        found.append({"label": balance.label, "remaining": balance.remaining, "perPull": balance.per_pull, "floor": balance.floor})
    return found
