"""Fires each desk's cycle at its stored target.

The target is recomputed only after it fired or when the schedule changed. An
hourly target recomputed at the full hour would already point to the next hour
and the run would be skipped, which is exactly what happened before this file.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime

from intel import desks
from intel.cycle import Runner, last_cycle_key
from intel.desk import Desk
from intel.store import Store

_TICK = 2.0


def next_run_key(desk_id: str) -> str:
    return f"{desk_id}:next_run_at"


def retarget(store: Store, desk: Desk) -> str:
    """Compute and store the next target from the desk's schedule. Called when the schedule changes."""
    target = desk.schedule.next_run_iso(store.get_state(last_cycle_key(desk.id)))
    store.set_state(next_run_key(desk.id), target)
    return target


def next_run_at(store: Store, desk: Desk) -> str:
    if not desk.schedule.enabled:
        return ""
    return store.get_state(next_run_key(desk.id)) or retarget(store, desk)


def start(store: Store, runner: Runner) -> None:
    threading.Thread(target=_loop, args=(store, runner), daemon=True).start()


def _loop(store: Store, runner: Runner) -> None:
    while True:
        wait = _TICK
        for desk in desks.all_desks(store):
            if not desk.schedule.enabled:
                store.set_state(next_run_key(desk.id), "")
                continue
            remaining = _seconds_until(next_run_at(store, desk))
            if remaining > 0:
                wait = min(wait, remaining)
                continue
            if runner.start(desk):
                retarget(store, desk)
        time.sleep(max(0.2, wait))


def _seconds_until(stamp: str) -> float:
    if not stamp:
        return _TICK
    try:
        target = datetime.fromisoformat(stamp)
    except ValueError:
        return 0.0
    return (target - datetime.now().astimezone()).total_seconds()
