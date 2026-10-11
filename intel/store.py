"""SQLite behind everything that is not a vector: desks, run log, Jev answers, state, secrets."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

LEGACY_DESK = "crypto-desk"

_SCHEMA = """
create table if not exists desks (
  id text primary key,
  body text not null
);
create table if not exists seen (
  id text primary key
);
create table if not exists runs (
  id integer primary key,
  desk text not null default '',
  source text not null,
  started_at text not null,
  finished_at text not null,
  status text not null,
  fetched integer not null,
  inserted integer not null,
  note text not null
);
create table if not exists answers (
  id integer primary key,
  desk text not null default '',
  asked_at text not null,
  question text not null,
  status text not null,
  verdict text not null,
  model text not null,
  max_score real,
  usage_json text not null,
  passages_json text not null,
  signature text not null,
  note text not null,
  batch text not null default ''
);
create table if not exists state (
  key text primary key,
  value text not null
);
create table if not exists secrets (
  key text primary key,
  value text not null
);
create table if not exists users (
  id text primary key,
  email text not null unique,
  name text not null,
  password_hash text not null,
  role text not null,
  status text not null,
  created_at text not null
);
create table if not exists sessions (
  token_hash text primary key,
  user_id text not null,
  expires_at text not null
);
"""


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(_SCHEMA)
            _migrate(db)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Commit on success, roll back on error, close either way."""
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("pragma journal_mode=wal")
        try:
            with db:
                yield db
        finally:
            db.close()

    # Desks

    def desks(self) -> dict[str, dict[str, object]]:
        with self._connect() as db:
            rows = db.execute("select id, body from desks order by rowid").fetchall()
        found: dict[str, dict[str, object]] = {}
        for row in rows:
            try:
                parsed = json.loads(row["body"])
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                found[str(row["id"])] = parsed
        return found

    def save_desk(self, desk_id: str, body: dict[str, object]) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "insert into desks (id, body) values (?, ?) on conflict(id) do update set body = excluded.body",
                (desk_id, json.dumps(body)),
            )

    def delete_desk(self, desk_id: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("delete from desks where id = ?", (desk_id,))
            db.execute("delete from runs where desk = ?", (desk_id,))
            db.execute("delete from answers where desk = ?", (desk_id,))
            db.execute("delete from state where key like ?", (f"{desk_id}:%",))

    # Ingest bookkeeping

    def known_ids(self) -> set[str]:
        with self._connect() as db:
            rows = db.execute("select id from seen").fetchall()
        return {str(row["id"]) for row in rows}

    def remember(self, ids: list[str]) -> None:
        if not ids:
            return
        with self._lock, self._connect() as db:
            db.executemany("insert or ignore into seen (id) values (?)", [(doc_id,) for doc_id in ids])

    def record_run(self, desk: str, source: str, started_at: str, status: str, fetched: int, inserted: int, note: str) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                """
                insert into runs (desk, source, started_at, finished_at, status, fetched, inserted, note)
                values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (desk, source, started_at, now_iso(), status, fetched, inserted, note[:500]),
            )

    def last_runs(self, desk: str) -> dict[str, sqlite3.Row]:
        with self._connect() as db:
            rows = db.execute(
                """
                select source, started_at, finished_at, status, fetched, inserted, note
                from runs
                where desk = ? and id in (select max(id) from runs where desk = ? group by source)
                """,
                (desk, desk),
            ).fetchall()
        return {str(row["source"]): row for row in rows}

    # Jev answers

    def save_answer(
        self,
        *,
        desk: str,
        question: str,
        status: str,
        verdict: str,
        model: str,
        max_score: float | None,
        usage: dict[str, int],
        passages: list[dict[str, object]],
        signature: str,
        note: str,
        batch: str,
    ) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                """
                insert into answers
                  (desk, asked_at, question, status, verdict, model, max_score, usage_json, passages_json, signature, note, batch)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (desk, now_iso(), question, status, verdict, model, max_score, json.dumps(usage), json.dumps(passages), signature, note[:500], batch),
            )

    def recent_answers(self, desk: str, limit: int = 8) -> list[dict[str, object]]:
        with self._connect() as db:
            rows = db.execute(
                """
                select id, asked_at, question, status, verdict, model, max_score,
                       usage_json, passages_json, signature, note, batch
                from answers where desk = ? order by id desc limit ?
                """,
                (desk, limit),
            ).fetchall()
        return [_answer(row) for row in rows]

    def latest_batch(self, desk: str) -> list[dict[str, object]]:
        """All answers from the most recent Jev call of this desk, oldest first."""
        recent = self.recent_answers(desk, 120)
        if not recent:
            return []
        batch = str(recent[0].get("batch") or "")
        rows = [answer for answer in recent if answer.get("batch") == batch] if batch else recent[:1]
        rows.reverse()
        return rows

    # Key-value state and secrets

    def get_state(self, key: str) -> str | None:
        with self._connect() as db:
            row = db.execute("select value from state where key = ?", (key,)).fetchone()
        return None if row is None else str(row["value"])

    def set_state(self, key: str, value: str) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "insert into state (key, value) values (?, ?) on conflict(key) do update set value = excluded.value",
                (key, value),
            )

    def secret_map(self) -> dict[str, str]:
        with self._connect() as db:
            rows = db.execute("select key, value from secrets").fetchall()
        return {str(row["key"]): str(row["value"]) for row in rows}

    def set_secret(self, key: str, value: str) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "insert into secrets (key, value) values (?, ?) on conflict(key) do update set value = excluded.value",
                (key, value),
            )

    def delete_secret(self, key: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("delete from secrets where key = ?", (key,))

    # Accounts. Passwords and session tokens are already hashed by the caller.

    def user_count(self) -> int:
        with self._connect() as db:
            row = db.execute("select count(*) as n from users").fetchone()
        return int(row["n"])

    def users(self) -> list[dict[str, object]]:
        with self._connect() as db:
            rows = db.execute("select * from users order by created_at").fetchall()
        return [_user(row) for row in rows]

    def user_by_email(self, email: str) -> dict[str, object] | None:
        with self._connect() as db:
            row = db.execute("select * from users where email = ?", (email,)).fetchone()
        return None if row is None else _user(row)

    def user_by_id(self, user_id: str) -> dict[str, object] | None:
        with self._connect() as db:
            row = db.execute("select * from users where id = ?", (user_id,)).fetchone()
        return None if row is None else _user(row)

    def insert_user(self, user: dict[str, object]) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                """
                insert into users (id, email, name, password_hash, role, status, created_at)
                values (?, ?, ?, ?, ?, ?, ?)
                """,
                (user["id"], user["email"], user["name"], user["passwordHash"], user["role"], user["status"], user["createdAt"]),
            )

    def set_user(self, user_id: str, role: str, status: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("update users set role = ?, status = ? where id = ?", (role, status, user_id))

    def delete_user(self, user_id: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("delete from sessions where user_id = ?", (user_id,))
            db.execute("delete from users where id = ?", (user_id,))

    def put_session(self, token_hash: str, user_id: str, expires_at: str) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "insert into sessions (token_hash, user_id, expires_at) values (?, ?, ?)",
                (token_hash, user_id, expires_at),
            )

    def session_user(self, token_hash: str) -> dict[str, object] | None:
        with self._lock, self._connect() as db:
            row = db.execute(
                """
                select users.* from sessions
                join users on users.id = sessions.user_id
                where sessions.token_hash = ?
                """,
                (token_hash,),
            ).fetchone()
            if row is None:
                return None
            expires = db.execute("select expires_at from sessions where token_hash = ?", (token_hash,)).fetchone()
            if expires is None or str(expires["expires_at"]) <= now_iso():
                db.execute("delete from sessions where token_hash = ?", (token_hash,))
                return None
        return _user(row)

    def delete_session(self, token_hash: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("delete from sessions where token_hash = ?", (token_hash,))

    def delete_user_sessions(self, user_id: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("delete from sessions where user_id = ?", (user_id,))


def _migrate(db: sqlite3.Connection) -> None:
    """Databases from before desks existed: everything they hold belonged to the crypto desk."""
    for table in ("runs", "answers"):
        columns = {row[1] for row in db.execute(f"pragma table_info({table})")}
        if "desk" not in columns:
            db.execute(f"alter table {table} add column desk text not null default ''")
        if table == "answers" and "batch" not in columns:
            db.execute("alter table answers add column batch text not null default ''")
        db.execute(f"update {table} set desk = ? where desk = ''", (LEGACY_DESK,))
    renamed = {
        "fomo_credits_remaining": "shared:fomo_credits_remaining",
        "fomo_cursor": f"{LEGACY_DESK}:fomo-alerts:cursor",
        "last_cycle_at": f"{LEGACY_DESK}:last_cycle_at",
        "jev_signature": f"{LEGACY_DESK}:jev_signature",
    }
    for old, new in renamed.items():
        db.execute("insert or ignore into state (key, value) select ?, value from state where key = ?", (new, old))
        db.execute("delete from state where key = ?", (old,))
    db.execute("delete from state where key = 'next_run_at'")


def _user(row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": str(row["id"]),
        "email": str(row["email"]),
        "name": str(row["name"]),
        "passwordHash": str(row["password_hash"]),
        "role": str(row["role"]),
        "status": str(row["status"]),
        "createdAt": str(row["created_at"]),
    }


def _answer(row: sqlite3.Row) -> dict[str, object]:
    usage = json.loads(row["usage_json"] or "{}")
    passages = json.loads(row["passages_json"] or "[]")
    return {
        "id": int(row["id"]),
        "askedAt": row["asked_at"],
        "question": row["question"],
        "status": row["status"],
        "verdict": row["verdict"],
        "model": row["model"],
        "maxScore": row["max_score"],
        "usage": usage if isinstance(usage, dict) else {},
        "passages": passages if isinstance(passages, list) else [],
        "signature": row["signature"],
        "note": row["note"],
        "batch": row["batch"],
    }
