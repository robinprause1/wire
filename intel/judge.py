"""One Jev call per desk per cycle. Each subject becomes a yes/no with a probability."""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
import uuid
from typing import Literal

from intel.desk import Desk
from intel.index import WireIndex
from intel.store import Store
from intel.subjects import Subject, picker_for

JEV_SECRET = "TYPESAFE_API_KEY"
MODEL_KEY = "JEV_MODEL"
DEFAULT_MODEL = "jev-latest"
_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
_CHUNK = 20

Packed = list[tuple[Subject, list[str]]]


def decision_of(yes: float, bar: float) -> tuple[Literal["yes", "no"], float]:
    """Map Jev's P(yes) onto the answer shown in the table and that answer's probability."""
    if yes >= bar:
        return "yes", yes
    return "no", 1.0 - yes


def model_name() -> str:
    return os.environ.get(MODEL_KEY, DEFAULT_MODEL)


def ask_jev(store: Store, index: WireIndex, desk: Desk, *, force: bool) -> None:
    picker = picker_for(desk, index)
    documents = index.documents(desk.id, desk.brief.window_hours)
    batch = uuid.uuid4().hex
    subjects = picker.pick(documents)
    if not subjects:
        _save_empty(store, desk, batch, f"No candidates in the last {desk.brief.window_hours} hours.")
        return
    packed: Packed = [(subject, picker.notes(documents, subject)) for subject in subjects]
    packed = [(subject, notes) for subject, notes in packed if notes]
    if not packed:
        _save_empty(store, desk, batch, "No candidate has a readable passage.")
        return
    signature = _signature(desk, packed)
    if not force and store.get_state(_signature_key(desk)) == signature and _fresh(store, desk):
        return
    api_key = os.environ.get(JEV_SECRET, "").strip()
    if not api_key:
        missing = {subject.key: f"{JEV_SECRET} is missing." for subject, _notes in packed}
        _save_each(store, desk, packed, batch, signature, model="", usage={}, answers={}, failed=missing)
        return
    answers: dict[str, object] = {}
    failed: dict[str, str] = {}
    model = ""
    input_tokens = 0
    output_tokens = 0
    requests = 0
    for chunk in _chunks(packed):
        try:
            body = _post(api_key, _payload(desk, chunk))
        except Exception as error:
            message = f"{type(error).__name__}: {error}"[:180]
            for subject, _notes in chunk:
                failed[subject.key] = message
            continue
        requests += 1
        part = body.get("answers")
        if isinstance(part, dict):
            answers.update(part)
        usage = body.get("usage")
        if isinstance(usage, dict):
            input_tokens += _int(usage.get("input_tokens"))
            output_tokens += _int(usage.get("output_tokens"))
        if isinstance(body.get("model"), str):
            model = str(body["model"])
    kept = {"requests": requests, "inputTokens": input_tokens, "outputTokens": output_tokens}
    _save_each(store, desk, packed, batch, signature, model=model, usage=kept, answers=answers, failed=failed)
    if not failed and len(answers) >= len(packed):
        store.set_state(_signature_key(desk), signature)


def _payload(desk: Desk, packed: Packed) -> dict[str, object]:
    return {
        "model": model_name(),
        "state": {"reports": [{"subject": subject.label, "notes": notes} for subject, notes in packed]},
        "questions": {
            subject.key: {
                "type": "noul",
                "instructions": desk.brief.instructions(subject.label),
                "criteria": {"true": desk.brief.when_true, "false": desk.brief.when_false},
            }
            for subject, _notes in packed
        },
    }


def _chunks(packed: Packed) -> list[Packed]:
    return [packed[start : start + _CHUNK] for start in range(0, len(packed), _CHUNK)]


def _save_empty(store: Store, desk: Desk, batch: str, note: str) -> None:
    store.save_answer(
        desk=desk.id,
        question=desk.brief.question,
        status="empty",
        verdict="pass",
        model="",
        max_score=None,
        usage={},
        passages=[],
        signature="",
        note=note,
        batch=batch,
    )


def _save_each(
    store: Store,
    desk: Desk,
    packed: Packed,
    batch: str,
    signature: str,
    *,
    model: str,
    usage: dict[str, int],
    answers: dict[str, object],
    failed: dict[str, str],
) -> None:
    for subject, notes in packed:
        verdict = "no"
        score: float | None = None
        mention = "story" if subject.mentions == 1 else "stories"
        row_note = f"{subject.mentions} {mention} · {', '.join(subject.sources)}"
        raw = answers.get(subject.key)
        yes = raw.get("noul") if isinstance(raw, dict) else None
        row_status = "ok"
        if subject.key in failed:
            row_status = "error"
            row_note = f"{row_note}. {failed[subject.key]}"
        elif isinstance(yes, bool) or not isinstance(yes, (int, float)) or not 0 <= float(yes) <= 1:
            row_status = "error"
            row_note = f"{row_note}. No probability."
        else:
            verdict, score = decision_of(float(yes), desk.brief.yes_at)
        store.save_answer(
            desk=desk.id,
            question=desk.brief.instructions(subject.label),
            status=row_status,
            verdict=verdict if row_status == "ok" else "pass",
            model=model,
            max_score=score,
            usage=usage,
            passages=[{"key": subject.key, "label": subject.label, "url": subject.url, "notes": notes, "yes": yes}],
            signature=signature,
            note=row_note,
            batch=batch,
        )


def _signature(desk: Desk, packed: Packed) -> str:
    raw = "|".join(f"{subject.key}:{';'.join(notes)}" for subject, notes in packed)
    return hashlib.sha256(f"{desk.brief.question}\n{raw}".encode()).hexdigest()


def _signature_key(desk: Desk) -> str:
    return f"{desk.id}:jev_signature"


def _fresh(store: Store, desk: Desk) -> bool:
    rows = store.latest_batch(desk.id)
    return bool(rows) and all(row.get("status") == "ok" for row in rows)


def _post(api_key: str, payload: dict[str, object]) -> dict[str, object]:
    request = urllib.request.Request(
        _ENDPOINT,
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            body = json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"HTTP {error.code}") from None
    if not isinstance(body, dict):
        raise RuntimeError("Jev returned no object.")
    return body


def _int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value
