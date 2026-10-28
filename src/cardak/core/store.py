"""SQLite store for agent sessions, shared by `cardak hook` processes and the deck.

Every hook call is its own short process, so writers coordinate through SQLite: WAL mode,
a busy timeout, and `BEGIN IMMEDIATE` around each read-modify-write.
"""

import contextlib
import json
import sqlite3
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import astuple, dataclass, fields
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

from cardak.core.agents import SessionState, mark_seen
from cardak.core.config import state_dir

if TYPE_CHECKING:  # the hook imports this module; it never reads transcripts
    from cardak.collectors.usage import Prompt, Reply, Usage

SCHEMA_VERSION = 5
# The oldest schema whose code can still use this database, kept in PRAGMA application_id so
# that an older cardak (from this one on) opens a newer file instead of refusing it. Only a
# new table is safe for older code. Any change to an existing table, a new column included,
# must raise this: older code reads rows with `SessionState(**row)`, inserts positionally and
# replaces whole rows.
MIN_READER = 1
KEEP_SECONDS = 7 * 86_400
RESOURCE_KEEP_SECONDS = 30 * 86_400
SUMMARY_KEEP_SECONDS = 62 * 86_400  # longer than a month: the spend cap counts them
USAGE_KEEP_SECONDS = 62 * 86_400  # the same: this month's spend at API prices (#29)
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
CREATE TABLE IF NOT EXISTS summaries (
    session_id TEXT NOT NULL,
    wait_since REAL NOT NULL,
    text TEXT,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cost_usd REAL NOT NULL,
    created_at REAL NOT NULL,
    error TEXT,
    PRIMARY KEY (session_id, wait_since)
);
CREATE INDEX IF NOT EXISTS summaries_created ON summaries (created_at);
CREATE TABLE IF NOT EXISTS resource_events (
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    level TEXT NOT NULL,
    subject TEXT NOT NULL,
    text TEXT NOT NULL,
    notified INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS resource_events_ts ON resource_events (ts);
CREATE TABLE IF NOT EXISTS resource_minutes (
    minute INTEGER NOT NULL,
    subject TEXT NOT NULL,
    name TEXT NOT NULL,
    ram_mean INTEGER NOT NULL,
    ram_max INTEGER NOT NULL,
    cpu_mean REAL NOT NULL,
    cpu_max REAL NOT NULL,
    PRIMARY KEY (subject, minute)
);
CREATE INDEX IF NOT EXISTS resource_minutes_minute ON resource_minutes (minute);
-- covering indexes: a median is one ordered index walk per subject, no sort
CREATE INDEX IF NOT EXISTS resource_minutes_ram ON resource_minutes (subject, ram_mean, minute);
CREATE INDEX IF NOT EXISTS resource_minutes_cpu ON resource_minutes (subject, cpu_mean, minute);
CREATE TABLE IF NOT EXISTS resource_days (
    subject TEXT NOT NULL,
    day TEXT NOT NULL,
    name TEXT NOT NULL,
    ram_peak INTEGER NOT NULL,
    cpu_peak REAL NOT NULL,
    PRIMARY KEY (subject, day)
);
CREATE TABLE IF NOT EXISTS dismissed_reports (ts REAL PRIMARY KEY);
CREATE TABLE IF NOT EXISTS claude_replies (
    reply_key TEXT PRIMARY KEY,
    ts REAL NOT NULL,
    session_id TEXT,
    cwd TEXT,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cache_read_tokens INTEGER NOT NULL,
    cache_write_5m_tokens INTEGER NOT NULL,
    cache_write_1h_tokens INTEGER NOT NULL,
    speed TEXT
);
CREATE INDEX IF NOT EXISTS claude_replies_ts ON claude_replies (ts);
-- stored for per-project stats (#88): nothing reads it yet
CREATE TABLE IF NOT EXISTS claude_prompts (
    prompt_key TEXT PRIMARY KEY,
    ts REAL NOT NULL,
    session_id TEXT,
    cwd TEXT,
    chars INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS claude_prompts_ts ON claude_prompts (ts);
CREATE TABLE IF NOT EXISTS transcript_offsets (
    path TEXT PRIMARY KEY,
    inode INTEGER NOT NULL,
    offset INTEGER NOT NULL
);
"""
ALERT_KINDS = ("memory", "big", "growth", "busy", "machine_busy")


@dataclass(frozen=True)
class SummaryRow:
    """One waiting summary: `wait_since` is the session's status_since for that wait."""

    session_id: str
    wait_since: float
    text: str | None  # None: the model's output was rejected
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    created_at: float
    error: str | None = None  # why no summary could be made (API error, key problem)


@dataclass(frozen=True)
class ResourceEvent:
    """A resource alert as the daemon logged it. Primitives only: the hook imports this module."""

    ts: float
    kind: str  # "memory" | "big" | "growth" | "busy" | "machine_busy" | "crash" | "daemon"
    level: str  # "warning" | "critical" | "info"
    subject: str  # "machine" | "project:<path>" | "pane:<id>" | "wezterm:<pid>" | "daemon"
    text: str
    notified: bool


@dataclass(frozen=True)
class MinuteRow:
    """One minute of one project's (or Other's) usage, as the daemon rolls it up."""

    minute: int  # int(ts // 60)
    subject: str  # "project:<path>" | "other"
    name: str
    ram_mean: int
    ram_max: int
    cpu_mean: float
    cpu_max: float


@dataclass(frozen=True)
class DayPeak:
    subject: str
    name: str  # from the subject's newest row
    day: str  # local YYYY-MM-DD
    ram_peak: int
    cpu_peak: float


class _Gone(Exception):
    """The session's row went away inside a transaction; the update rolls back."""


class StoreError(Exception):
    pass


def _local_day(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


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
            if version > SCHEMA_VERSION and not self._readable():
                self._con.close()
                raise StoreError(f"{path} is from a newer cardak (schema {version})")
            self._con.execute("PRAGMA journal_mode = WAL")
            if version < SCHEMA_VERSION:
                self._con.executescript(_SCHEMA)
                # the reader floor first: no moment where a raised schema shows the old floor
                self._con.execute(f"PRAGMA application_id = {MIN_READER}")
                self._con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            elif version == SCHEMA_VERSION and self._min_reader() != MIN_READER:
                # only when it differs: an open must not need the write lock (hooks, read-only)
                self._con.execute(f"PRAGMA application_id = {MIN_READER}")
        except (OSError, sqlite3.Error) as e:
            raise StoreError(f"cannot open {path}: {e}") from e

    def _min_reader(self) -> int:
        return self._con.execute("PRAGMA application_id").fetchone()[0]

    def _readable(self) -> bool:
        """A newer database that says this schema's code can still use it (0: says nothing)."""
        return 1 <= self._min_reader() <= SCHEMA_VERSION

    def close(self) -> None:
        self._con.close()

    def __del__(self) -> None:
        con = getattr(self, "_con", None)  # absent when __init__ failed
        if con is not None:
            con.close()

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

    def mark_seen(self, session_id: str, now: float, wait_since: float | None = None) -> None:
        """Mark the wait seen; with wait_since, only if it is still that wait (a hook may have
        moved the session into a newer one since the deck read it)."""
        try:
            if self._get(session_id) is None:
                return
        except sqlite3.Error as e:
            raise StoreError(str(e)) from e

        def seen(old: SessionState | None) -> tuple[SessionState, None]:
            if old is None:
                raise _Gone  # a hook pruned it since the check above
            if wait_since is not None and old.status_since != wait_since:
                return old, None
            return mark_seen(old, now), None

        with contextlib.suppress(_Gone):
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

    def event_at(self, session_id: str, ts: float) -> tuple[str, str] | None:
        """The event (name, payload) a session recorded at exactly `ts`."""
        try:
            row = self._con.execute(
                "SELECT event, payload_json FROM events WHERE session_id = ? AND ts = ? LIMIT 1",
                (session_id, ts),
            ).fetchone()
        except sqlite3.Error as e:
            raise StoreError(str(e)) from e
        return (row[0], row[1]) if row else None

    def add_summary(self, row: SummaryRow) -> None:
        try:
            self._con.execute(
                "INSERT OR IGNORE INTO summaries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", astuple(row)
            )
        except sqlite3.Error as e:
            raise StoreError(str(e)) from e

    def has_summary(self, session_id: str, wait_since: float) -> bool:
        try:
            row = self._con.execute(
                "SELECT 1 FROM summaries WHERE session_id = ? AND wait_since = ?",
                (session_id, wait_since),
            ).fetchone()
        except sqlite3.Error as e:
            raise StoreError(str(e)) from e
        return row is not None

    def summaries_for(self, waits: Iterable[tuple[str, float]]) -> dict[str, str]:
        """session_id -> summary text, for the given waits that have one."""
        found: dict[str, str] = {}
        try:
            for session_id, wait_since in waits:
                row = self._con.execute(
                    "SELECT text FROM summaries WHERE session_id = ? AND wait_since = ?",
                    (session_id, wait_since),
                ).fetchone()
                if row and row[0] is not None:
                    found[session_id] = row[0]
        except sqlite3.Error as e:
            raise StoreError(str(e)) from e
        return found

    def latest_error_since(self, ts: float) -> str | None:
        """The newest summary attempt's error, if it failed; None if it worked or none ran."""
        try:
            row = self._con.execute(
                "SELECT error FROM summaries WHERE created_at >= ? "
                "ORDER BY created_at DESC LIMIT 1",
                (ts,),
            ).fetchone()
        except sqlite3.Error as e:
            raise StoreError(str(e)) from e
        return row[0] if row else None

    def spend_since(self, ts: float) -> float:
        try:
            row = self._con.execute(
                "SELECT COALESCE(SUM(cost_usd), 0.0) FROM summaries WHERE created_at >= ?", (ts,)
            ).fetchone()
        except sqlite3.Error as e:
            raise StoreError(str(e)) from e
        return float(row[0])

    def record_resource_event(self, event: ResourceEvent) -> None:
        try:
            self._con.execute(
                "INSERT INTO resource_events VALUES (?, ?, ?, ?, ?, ?)",
                (event.ts, event.kind, event.level, event.subject, event.text, int(event.notified)),
            )
        except sqlite3.Error as e:
            raise StoreError(str(e)) from e

    def resource_events_since(self, ts: float) -> list[ResourceEvent]:
        """Oldest first."""
        try:
            rows = self._con.execute(
                "SELECT ts, kind, level, subject, text, notified FROM resource_events "
                "WHERE ts >= ? ORDER BY ts, rowid",
                (ts,),
            ).fetchall()
        except sqlite3.Error as e:
            raise StoreError(str(e)) from e
        return [ResourceEvent(r[0], r[1], r[2], r[3], r[4], bool(r[5])) for r in rows]

    def _query(self, sql: str, args: tuple[object, ...] = ()) -> list[sqlite3.Row]:
        try:
            return self._con.execute(sql, args).fetchall()
        except sqlite3.Error as e:
            raise StoreError(str(e)) from e

    def record_minutes(self, rows: Sequence[MinuteRow]) -> None:
        try:
            with self._con:
                self._con.execute("BEGIN")
                self._con.executemany(
                    "INSERT OR REPLACE INTO resource_minutes VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [astuple(r) for r in rows],
                )
                # Daily peaks kept as the minutes arrive: the history view reads ~30 rows per
                # project instead of grouping ~43k minutes each into local days.
                self._con.executemany(
                    "INSERT INTO resource_days VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT (subject, day) DO UPDATE SET name = excluded.name, "
                    "ram_peak = max(ram_peak, excluded.ram_peak), "
                    "cpu_peak = max(cpu_peak, excluded.cpu_peak)",
                    [(r.subject, _local_day(r.minute * 60), r.name, r.ram_max, r.cpu_max)
                     for r in rows],
                )  # fmt: skip
        except sqlite3.Error as e:
            raise StoreError(str(e)) from e

    def history_days(self, since: float) -> list[DayPeak]:
        rows = self._query(
            "SELECT d.subject, (SELECT name FROM resource_days n WHERE n.subject = d.subject "
            "ORDER BY day DESC LIMIT 1), d.day, d.ram_peak, d.cpu_peak FROM resource_days d "
            "WHERE d.day >= ? ORDER BY d.subject, d.day",
            (_local_day(since),),
        )
        return [DayPeak(r[0], r[1], r[2], int(r[3]), float(r[4])) for r in rows]

    def history_medians(self, since: float) -> dict[str, tuple[float, float]]:
        """subject → (median RAM, median CPU) over the per-minute means; the lower middle."""
        start = int(since // 60)
        counts = self._query(
            "SELECT subject, count(*) FROM resource_minutes WHERE minute >= ? GROUP BY subject",
            (start,),
        )
        out: dict[str, tuple[float, float]] = {}
        for subject, n in counts:
            middle = []
            for column in ("ram_mean", "cpu_mean"):
                row = self._query(
                    f"SELECT {column} FROM resource_minutes WHERE subject = ? AND minute >= ? "
                    f"ORDER BY {column} LIMIT 1 OFFSET ?",
                    (subject, start, (n - 1) // 2),
                )
                middle.append(float(row[0][0]))
            out[subject] = (middle[0], middle[1])
        return out

    def history_hours(self, subject: str, since: float) -> list[tuple[int, int, float]]:
        """(hour start, RAM peak, CPU peak), oldest first."""
        rows = self._query(
            "SELECT minute / 60 * 3600 AS hour, max(ram_max), max(cpu_max) FROM resource_minutes "
            "WHERE subject = ? AND minute >= ? GROUP BY hour ORDER BY hour",
            (subject, int(since // 60)),
        )
        return [(int(r[0]), int(r[1]), float(r[2])) for r in rows]

    def alert_counts(self, since: float) -> dict[str, int]:
        """Notified resource alerts per subject (not crashes or daemon lines)."""
        marks = ", ".join("?" * len(ALERT_KINDS))
        rows = self._query(
            "SELECT subject, count(*) FROM resource_events WHERE ts >= ? AND notified = 1 "
            f"AND kind IN ({marks}) GROUP BY subject",
            (since, *ALERT_KINDS),
        )
        return {r[0]: int(r[1]) for r in rows}

    def latest_crash(self, since: float) -> ResourceEvent | None:
        """The newest loud crash report not yet dismissed in the deck."""
        rows = self._query(
            "SELECT ts, kind, level, subject, text, notified FROM resource_events "
            "WHERE ts >= ? AND kind = 'crash' AND level = 'critical' "
            "AND ts NOT IN (SELECT ts FROM dismissed_reports) ORDER BY ts DESC LIMIT 1",
            (since,),
        )
        if not rows:
            return None
        r = rows[0]
        return ResourceEvent(r[0], r[1], r[2], r[3], r[4], bool(r[5]))

    def dismiss_report(self, ts: float) -> None:
        self._query("INSERT OR IGNORE INTO dismissed_reports VALUES (?)", (ts,))

    def record_usage(self, path: str, inode: int, offset: int, usage: "Usage") -> int:
        """Store what a pass read from one transcript, and how far it got; new replies counted.

        The deck and the daemon may both read the same files: a reply or prompt already stored
        is ignored, so the two never double the figure.
        """
        try:
            with self._con:
                self._con.execute("BEGIN")
                before = self._con.total_changes
                self._con.executemany(
                    "INSERT OR IGNORE INTO claude_replies VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [astuple(r) for r in usage.replies],
                )
                added = self._con.total_changes - before
                self._con.executemany(
                    "INSERT OR IGNORE INTO claude_prompts VALUES (?, ?, ?, ?, ?)",
                    [astuple(p) for p in usage.prompts],
                )
                self._con.execute(
                    "INSERT OR REPLACE INTO transcript_offsets VALUES (?, ?, ?)",
                    (path, inode, offset),
                )
        except sqlite3.Error as e:
            raise StoreError(str(e)) from e
        return added

    def usage_mark(self) -> tuple[int, int]:
        """The newest stored reply and prompt: a figure built on the same mark is still current,
        whichever process stored the rows."""
        rows = self._query(
            "SELECT (SELECT max(rowid) FROM claude_replies),"
            " (SELECT max(rowid) FROM claude_prompts)"
        )
        return (rows[0][0] or 0, rows[0][1] or 0)

    def transcript_offsets(self) -> dict[str, tuple[int, int]]:
        """path -> (inode, offset) of every transcript a pass has read."""
        rows = self._query("SELECT path, inode, offset FROM transcript_offsets")
        return {r[0]: (int(r[1]), int(r[2])) for r in rows}

    def replies_since(self, ts: float) -> list["Reply"]:
        """Oldest first."""
        from cardak.collectors.usage import Reply

        rows = self._query("SELECT * FROM claude_replies WHERE ts >= ? ORDER BY ts, rowid", (ts,))
        return [Reply(*r) for r in rows]

    def prompts_since(self, ts: float) -> list["Prompt"]:
        """Oldest first."""
        from cardak.collectors.usage import Prompt

        rows = self._query("SELECT * FROM claude_prompts WHERE ts >= ? ORDER BY ts, rowid", (ts,))
        return [Prompt(*r) for r in rows]

    def event_count(self) -> int:
        return self._con.execute("SELECT count(*) FROM events").fetchone()[0]

    def prune(self, now: float) -> None:
        cutoff = now - KEEP_SECONDS
        try:
            self._con.execute(
                "DELETE FROM agent_sessions WHERE ended_at IS NOT NULL AND ended_at < ?", (cutoff,)
            )
            # killed without a SessionEnd: after a restart its pane id goes to someone else
            self._con.execute(
                "DELETE FROM agent_sessions WHERE ended_at IS NULL AND updated_at < ?", (cutoff,)
            )
            self._con.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
            self._con.execute(
                "DELETE FROM summaries WHERE created_at < ?", (now - SUMMARY_KEEP_SECONDS,)
            )
            usage_cutoff = now - USAGE_KEEP_SECONDS
            self._con.execute("DELETE FROM claude_replies WHERE ts < ?", (usage_cutoff,))
            self._con.execute("DELETE FROM claude_prompts WHERE ts < ?", (usage_cutoff,))
            resource_cutoff = now - RESOURCE_KEEP_SECONDS
            self._con.execute("DELETE FROM resource_events WHERE ts < ?", (resource_cutoff,))
            self._con.execute(
                "DELETE FROM resource_minutes WHERE minute < ?", (int(resource_cutoff // 60),)
            )
            self._con.execute("DELETE FROM dismissed_reports WHERE ts < ?", (resource_cutoff,))
            self._con.execute(
                "DELETE FROM resource_days WHERE day < ?", (_local_day(resource_cutoff),)
            )
            self._con.execute(
                "DELETE FROM events WHERE rowid NOT IN "
                "(SELECT rowid FROM events ORDER BY ts DESC LIMIT ?)",
                (MAX_EVENTS,),
            )
        except sqlite3.Error as e:
            raise StoreError(str(e)) from e
