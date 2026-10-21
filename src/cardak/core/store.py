"""SQLite store for agent sessions, shared by `cardak hook` processes and the deck.

Every hook call is its own short process, so writers coordinate through SQLite: WAL mode,
a busy timeout, and `BEGIN IMMEDIATE` around each read-modify-write.
"""

import json
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import astuple, fields
from pathlib import Path
from typing import TypeVar

from cardak.core.agents import SessionState, mark_seen
from cardak.core.config import state_dir

SCHEMA_VERSION = 1
KEEP_SECONDS = 7 * 86_400
MAX_EVENTS = 10_000
BUSY_TIMEOUT = 2.0

T = TypeVar("T")

_COLUMNS = [f.name for f in fields(SessionState)]
_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS agent_sessions (
    session_id TEXT PRIMARY KEY,
    {", ".join(c for c in _COLUMNS if c != "session_id")}
);
CREATE INDEX IF NOT EXISTS agent_sessions_live ON agent_sessions (ended_at);
CREATE TABLE IF NOT EXISTS events (
    ts REAL NOT NULL,
    session_id TEXT NOT NULL,
    event TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_ts ON events (ts);
"""


class StoreError(Exception):
    pass


def db_path(env: Mapping[str, str] | None = None) -> Path:
    return state_dir(env) / "cardak.db"


def _to_row(state: SessionState) -> tuple[object, ...]:
    row: list[object] = []
    for name, value in zip(_COLUMNS, astuple(state), strict=True):
        if isinstance(value, Path):
            value = str(value)
        elif name == "turn_files":
            value = json.dumps(list(value))
        elif isinstance(value, bool):
            value = int(value)
        row.append(value)
    return tuple(row)


def _from_row(row: sqlite3.Row) -> SessionState:
    data = dict(row)
    for name in ("cwd", "transcript_path"):
        data[name] = Path(data[name]) if data[name] is not None else None
    data["turn_files"] = tuple(json.loads(data["turn_files"] or "[]"))
    data["permission_requested"] = bool(data["permission_requested"])
    return SessionState(**data)


class Store:
    def __init__(self, path: Path) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # The deck calls in from worker threads, one at a time (LiveSource's lock).
            self._con = sqlite3.connect(
                path, timeout=BUSY_TIMEOUT, isolation_level=None, check_same_thread=False
            )
            self._con.row_factory = sqlite3.Row
            version = self._con.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                self._con.close()
                raise StoreError(f"{path} is from a newer cardak (schema {version})")
            self._con.execute("PRAGMA journal_mode = WAL")
            if version < SCHEMA_VERSION:
                self._con.executescript(_SCHEMA)
                self._con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        except (OSError, sqlite3.Error) as e:
            raise StoreError(f"cannot open {path}: {e}") from e

    def close(self) -> None:
        self._con.close()

    def _get(self, session_id: str) -> SessionState | None:
        row = self._con.execute(
            "SELECT * FROM agent_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        return _from_row(row) if row else None

    def get(self, session_id: str) -> SessionState | None:
        try:
            return self._get(session_id)
        except sqlite3.Error as e:
            raise StoreError(str(e)) from e

    def update(
        self, session_id: str, fn: Callable[[SessionState | None], tuple[SessionState, T]]
    ) -> T:
        """Read, transform and write one session atomically with respect to other writers."""
        try:
            self._con.execute("BEGIN IMMEDIATE")
        except sqlite3.Error as e:
            raise StoreError(str(e)) from e
        try:
            new, extra = fn(self._get(session_id))
            placeholders = ", ".join("?" for _ in _COLUMNS)
            self._con.execute(
                f"INSERT OR REPLACE INTO agent_sessions ({', '.join(_COLUMNS)}) "
                f"VALUES ({placeholders})",
                _to_row(new),
            )
            self._con.execute("COMMIT")
        except BaseException as e:
            self._con.execute("ROLLBACK")
            if isinstance(e, sqlite3.Error):
                raise StoreError(str(e)) from e
            raise
        return extra

    def mark_seen(self, session_id: str, now: float) -> None:
        try:
            if self._get(session_id) is None:
                return
        except sqlite3.Error as e:
            raise StoreError(str(e)) from e

        def seen(old: SessionState | None) -> tuple[SessionState, None]:
            assert old is not None  # rows are only deleted by prune, for ended sessions
            return mark_seen(old, now), None

        self.update(session_id, seen)

    def live_sessions(self) -> list[SessionState]:
        try:
            rows = self._con.execute(
                "SELECT * FROM agent_sessions WHERE ended_at IS NULL ORDER BY started_at"
            ).fetchall()
        except sqlite3.Error as e:
            raise StoreError(str(e)) from e
        return [_from_row(r) for r in rows]

    def record_event(self, session_id: str, event: str, payload: str, now: float) -> None:
        try:
            self._con.execute(
                "INSERT INTO events (ts, session_id, event, payload_json) VALUES (?, ?, ?, ?)",
                (now, session_id, event, payload),
            )
        except sqlite3.Error as e:
            raise StoreError(str(e)) from e

    def event_count(self) -> int:
        return self._con.execute("SELECT count(*) FROM events").fetchone()[0]

    def prune(self, now: float) -> None:
        cutoff = now - KEEP_SECONDS
        try:
            self._con.execute(
                "DELETE FROM agent_sessions WHERE ended_at IS NOT NULL AND ended_at < ?", (cutoff,)
            )
            self._con.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
            self._con.execute(
                "DELETE FROM events WHERE rowid NOT IN "
                "(SELECT rowid FROM events ORDER BY ts DESC LIMIT ?)",
                (MAX_EVENTS,),
            )
        except sqlite3.Error as e:
            raise StoreError(str(e)) from e
