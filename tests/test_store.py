import sqlite3
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from cardak.collectors.usage import Prompt, Reply, Usage
from cardak.core.agents import SessionState
from cardak.core.config import state_dir
from cardak.core.store import (
    MIN_READER,
    SCHEMA_VERSION,
    USAGE_KEEP_SECONDS,
    DayPeak,
    MinuteRow,
    ResourceEvent,
    Store,
    StoreError,
    SummaryRow,
    db_path,
)

DAY = 86_400.0


def put(store: Store, state: SessionState) -> None:
    store.update(state.session_id, lambda _old: (state, None))


def test_state_dir_follows_xdg() -> None:
    assert state_dir({"XDG_STATE_HOME": "/s", "HOME": "/h"}) == Path("/s/cardak")
    assert state_dir({"HOME": "/h"}) == Path("/h/.local/state/cardak")
    assert db_path({"HOME": "/h"}) == Path("/h/.local/state/cardak/cardak.db")


def test_round_trip(tmp_path: Path) -> None:
    store = Store(tmp_path / "db.sqlite")
    full = SessionState(
        "s1", pane_id=7, cwd=Path("/r"), transcript_path=Path("/t.jsonl"), status="waiting",
        kind="permission", status_since=1.5, reason="Bash: ls", last_prompt="hi",
        last_prompt_at=1.0, last_message="Done.", current_tool="Bash: ls", turn_edits=1,
        turn_reads=2, turn_commands=3, turn_other=4, turn_files=("/r/a.py", "/r/b.py"),
        permission_requested=True, seen_at=2.0, started_at=0.5, updated_at=2.5, ended_at=None,
    )  # fmt: skip
    put(store, full)
    put(store, SessionState("s2"))
    assert store.get("s1") == full
    assert store.get("s2") == SessionState("s2")
    assert store.get("nope") is None


def test_update_sees_the_old_state_and_returns_extra(tmp_path: Path) -> None:
    store = Store(tmp_path / "db")
    seen: list[SessionState | None] = []

    def fn(old: SessionState | None) -> tuple[SessionState, str]:
        seen.append(old)
        return SessionState("s1", turn_edits=len(seen)), "extra"

    assert store.update("s1", fn) == "extra"
    store.update("s1", fn)
    assert seen[0] is None and seen[1] == SessionState("s1", turn_edits=1)


def test_failed_update_rolls_back(tmp_path: Path) -> None:
    store = Store(tmp_path / "db")
    put(store, SessionState("s1", turn_edits=1))

    def boom(_old: SessionState | None) -> tuple[SessionState, None]:
        raise RuntimeError("x")

    with pytest.raises(RuntimeError):
        store.update("s1", boom)
    put(store, SessionState("s1", turn_edits=2))  # the connection is still usable
    assert store.get("s1") == SessionState("s1", turn_edits=2)


def test_creates_parent_dir_and_schema_version(tmp_path: Path) -> None:
    path = tmp_path / "deep" / "cardak.db"
    Store(path).close()
    con = sqlite3.connect(path)
    assert con.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 6
    assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    con.close()


def test_live_sessions_skips_ended(tmp_path: Path) -> None:
    store = Store(tmp_path / "db")
    put(store, SessionState("a"))
    put(store, SessionState("b", ended_at=5.0))
    assert [s.session_id for s in store.live_sessions()] == ["a"]


def test_mark_seen(tmp_path: Path) -> None:
    store = Store(tmp_path / "db")
    put(store, SessionState("a", status="waiting", kind="finished", status_since=1.0))
    store.mark_seen("a", 9.0)
    state = store.get("a")
    assert state is not None and state.status == "idle" and state.seen_at == 9.0
    store.mark_seen("missing", 9.0)  # no row: nothing happens
    assert store.get("missing") is None


def test_mark_seen_skips_a_newer_wait(tmp_path: Path) -> None:
    store = Store(tmp_path / "db")
    put(store, SessionState("a", status="waiting", kind="finished", status_since=10.0))
    store.mark_seen("a", 20.0, wait_since=5.0)  # the deck saw an older wait
    state = store.get("a")
    assert state is not None and state.status == "waiting" and state.seen_at is None
    store.mark_seen("a", 20.0, wait_since=10.0)
    state = store.get("a")
    assert state is not None and state.status == "idle" and state.seen_at == 20.0


def test_record_event_and_prune(tmp_path: Path) -> None:
    store = Store(tmp_path / "db")
    now = 100 * DAY
    put(store, SessionState("old", ended_at=now - 8 * DAY))
    put(store, SessionState("recent", ended_at=now - 1 * DAY))
    put(store, SessionState("live"))
    store.record_event("old", "Stop", "{}", now - 8 * DAY)
    store.record_event("live", "Stop", "{}", now)
    store.prune(now)
    assert store.get("old") is None and store.get("recent") is not None
    assert store.event_count() == 1


def test_prune_caps_events(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("cardak.core.store.MAX_EVENTS", 5)
    store = Store(tmp_path / "db")
    for i in range(8):
        store.record_event("s", "Stop", str(i), 1000.0 + i)
    store.prune(1010.0)
    assert store.event_count() == 5


def test_concurrent_update_waits(tmp_path: Path) -> None:
    path = tmp_path / "db"
    first, second = Store(path), Store(path)
    held = threading.Event()
    release = threading.Event()

    def slow(_old: SessionState | None) -> tuple[SessionState, None]:
        held.set()
        release.wait(2)
        return SessionState("s1", turn_edits=1), None

    t = threading.Thread(target=first.update, args=("s1", slow))
    t.start()
    held.wait(2)
    threading.Timer(0.3, release.set).start()
    start = time.monotonic()
    second.update("s2", lambda _old: (SessionState("s2"), None))
    assert time.monotonic() - start >= 0.2  # it waited for the first writer
    t.join()
    assert {s.session_id for s in second.live_sessions()} == {"s1", "s2"}


def newer(path: Path, min_reader: int) -> None:
    """Make path look written by a newer cardak that says which readers can still use it."""
    Store(path).close()
    con = sqlite3.connect(path)
    con.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 5}")
    con.execute(f"PRAGMA application_id = {min_reader}")
    con.close()


def test_newer_db_without_min_reader_refused(tmp_path: Path) -> None:
    newer(tmp_path / "db", 0)  # as every cardak up to 0.9.0 left it
    with pytest.raises(StoreError, match="from a newer cardak"):
        Store(tmp_path / "db")


def test_newer_db_needing_newer_reader_refused(tmp_path: Path) -> None:
    newer(tmp_path / "db", SCHEMA_VERSION + 1)
    with pytest.raises(StoreError, match="from a newer cardak"):
        Store(tmp_path / "db")


def test_newer_compatible_db_opens_untouched(tmp_path: Path) -> None:
    newer(tmp_path / "db", 1)
    store = Store(tmp_path / "db")
    put(store, SessionState("a", status="idle"))
    assert store.get("a") is not None
    store.close()
    con = sqlite3.connect(tmp_path / "db")
    assert con.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION + 5  # not lowered
    assert con.execute("PRAGMA application_id").fetchone()[0] == 1  # not rewritten


def test_new_store_records_min_reader(tmp_path: Path) -> None:
    Store(tmp_path / "db").close()
    con = sqlite3.connect(tmp_path / "db")
    assert con.execute("PRAGMA application_id").fetchone()[0] == MIN_READER == 1


def test_unopenable_path_raises_store_error(tmp_path: Path) -> None:
    (tmp_path / "file").write_text("x")
    with pytest.raises(StoreError):
        Store(tmp_path / "file" / "db")


def summary_row(
    sid: str = "s", since: float = 10.0, text: str | None = "did x; needs y",
    cost: float = 0.001, at: float = 100.0,
) -> SummaryRow:  # fmt: skip
    return SummaryRow(sid, since, text, "claude-haiku-5-5", 1500, 30, cost, at)


def test_summary_roundtrip(tmp_path: Path) -> None:
    store = Store(tmp_path / "db")
    store.add_summary(summary_row())
    store.add_summary(summary_row(sid="t", text=None))
    assert store.has_summary("s", 10.0) and store.has_summary("t", 10.0)
    assert not store.has_summary("s", 11.0)
    waits = [("s", 10.0), ("t", 10.0), ("s", 11.0)]
    assert store.summaries_for(waits) == {"s": "did x; needs y"}
    assert store.summaries_for([]) == {}


def test_add_summary_ignores_a_second_row_for_the_same_wait(tmp_path: Path) -> None:
    store = Store(tmp_path / "db")
    store.add_summary(summary_row(text="first"))
    store.add_summary(summary_row(text="second"))
    assert store.summaries_for([("s", 10.0)]) == {"s": "first"}


def test_spend_since(tmp_path: Path) -> None:
    store = Store(tmp_path / "db")
    assert store.spend_since(0.0) == 0.0
    store.add_summary(summary_row(sid="a", cost=0.25, at=50.0))
    store.add_summary(summary_row(sid="b", cost=0.5, at=150.0))
    assert store.spend_since(100.0) == 0.5


def test_event_at(tmp_path: Path) -> None:
    store = Store(tmp_path / "db")
    store.record_event("s", "PermissionRequest", '{"x": 1}', 12.5)
    assert store.event_at("s", 12.5) == ("PermissionRequest", '{"x": 1}')
    assert store.event_at("s", 12.6) is None
    assert store.event_at("t", 12.5) is None


def test_prune_keeps_summaries_62_days(tmp_path: Path) -> None:
    store = Store(tmp_path / "db")
    store.add_summary(summary_row(sid="old", at=0.0))
    store.add_summary(summary_row(sid="new", at=10 * DAY))
    store.prune(62 * DAY + 1)
    assert not store.has_summary("old", 10.0)
    assert store.has_summary("new", 10.0)


def test_v1_database_gains_summaries(tmp_path: Path) -> None:
    path = tmp_path / "db"
    Store(path).close()
    con = sqlite3.connect(path)
    con.execute("DROP TABLE summaries")
    con.execute("PRAGMA user_version = 1")
    con.commit()
    con.close()
    store = Store(path)
    store.add_summary(summary_row())
    assert store.has_summary("s", 10.0)
    assert SCHEMA_VERSION == 6


def test_latest_error_since(tmp_path: Path) -> None:
    store = Store(tmp_path / "db")
    assert store.latest_error_since(0.0) is None
    store.add_summary(summary_row(sid="a", at=50.0))
    failed = SummaryRow(
        "b", 10.0, None, "claude-haiku-5-5", 0, 0, 0.0, 150.0, "AuthenticationError: bad key"
    )
    store.add_summary(failed)
    assert store.latest_error_since(100.0) == "AuthenticationError: bad key"
    assert store.latest_error_since(200.0) is None  # nothing this period
    store.add_summary(summary_row(sid="c", at=160.0))
    assert store.latest_error_since(100.0) is None  # a newer summary succeeded


def test_resource_events_round_trip(tmp_path: Path) -> None:
    store = Store(tmp_path / "c.db")
    e = ResourceEvent(10.0, "big", "warning", "project:/r/webshop", "webshop · pytest …", True)
    store.record_resource_event(e)
    store.record_resource_event(replace(e, ts=20.0, notified=False))
    assert store.resource_events_since(15.0) == [replace(e, ts=20.0, notified=False)]
    assert store.resource_events_since(0.0)[0] == e


def test_resource_events_pruned_after_30_days(tmp_path: Path) -> None:
    store = Store(tmp_path / "c.db")
    store.record_resource_event(ResourceEvent(0.0, "memory", "critical", "machine", "x", True))
    store.record_resource_event(ResourceEvent(29 * DAY, "memory", "critical", "machine", "y", True))
    store.prune(31 * DAY)
    assert [e.text for e in store.resource_events_since(-1.0)] == ["y"]


def test_schema_2_database_gains_the_table(tmp_path: Path) -> None:
    path = tmp_path / "c.db"
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE events (ts REAL NOT NULL, session_id TEXT NOT NULL, "
        "event TEXT NOT NULL, payload_json TEXT NOT NULL);"
        "INSERT INTO events VALUES (1.0, 's', 'Stop', '{}');"
        "PRAGMA user_version = 2;"
    )
    con.close()
    store = Store(path)
    assert store._con.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 6
    store.record_resource_event(ResourceEvent(1.0, "big", "warning", "pane:3", "x", False))
    assert store.event_count() == 1 and len(store.resource_events_since(0.0)) == 1


def test_resource_event_errors_become_store_errors(tmp_path: Path) -> None:
    store = Store(tmp_path / "c.db")
    store.close()
    with pytest.raises(StoreError):
        store.record_resource_event(ResourceEvent(1.0, "big", "warning", "pane:3", "x", False))
    with pytest.raises(StoreError):
        store.resource_events_since(0.0)


WEB = "project:/home/user/repos/webshop"
GIB = 2**30


def minute(
    m: int, ram: int, cpu: float = 10.0, subject: str = WEB, name: str = "webshop"
) -> MinuteRow:
    return MinuteRow(m, subject, name, ram, ram + 1, cpu, cpu + 1.0)


@pytest.fixture
def utc(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def test_minutes_roundtrip_days_and_medians(tmp_path: Path, utc: None) -> None:
    store = Store(tmp_path / "db")
    day2 = int(DAY // 60)
    store.record_minutes([minute(0, 1), minute(1, 5), minute(2, 3), minute(day2, 7, name="shop")])
    store.record_minutes([minute(1, 6)])  # the same minute again replaces it
    store.record_minutes([minute(0, 100, subject="other", name="Other")])
    assert sorted(store.history_days(0.0), key=lambda d: (d.subject, d.day)) == [
        DayPeak("other", "Other", "1970-01-01", 101, 11.0),
        DayPeak(WEB, "shop", "1970-01-01", 7, 11.0),
        DayPeak(WEB, "shop", "1970-01-02", 8, 11.0),
    ]
    medians = store.history_medians(0.0)
    assert medians[WEB] == (3.0, 10.0)  # ram_mean 1, 3, 6, 7: the lower middle
    assert medians["other"] == (100.0, 10.0)
    assert store.history_days(DAY)[0].day == "1970-01-02"


def test_days_are_local(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", "Europe/Berlin")
    time.tzset()
    try:
        store = Store(tmp_path / "db")
        late = 1792625400  # 2026-10-21 23:30 UTC, 01:30 on the 22nd in Berlin
        store.record_minutes([minute(late // 60, 1)])
        assert [d.day for d in store.history_days(0.0)] == ["2026-10-22"]
    finally:
        monkeypatch.undo()
        time.tzset()


def test_history_hours_buckets_by_hour(tmp_path: Path) -> None:
    store = Store(tmp_path / "db")
    store.record_minutes([minute(0, 1, 5.0), minute(59, 9, 1.0), minute(60, 2, 2.0)])
    store.record_minutes([minute(0, 50, subject="other")])
    assert store.history_hours(WEB, 0.0) == [(0, 10, 6.0), (3600, 3, 3.0)]
    assert store.history_hours(WEB, 3600.0) == [(3600, 3, 3.0)]


def test_alert_counts_counts_notified_only(tmp_path: Path) -> None:
    store = Store(tmp_path / "db")
    for ts, kind, notified in [(1.0, "big", True), (2.0, "big", False), (3.0, "busy", True),
                               (4.0, "crash", True)]:  # fmt: skip
        store.record_resource_event(ResourceEvent(ts, kind, "warning", WEB, "x", notified))
    store.record_resource_event(ResourceEvent(5.0, "memory", "warning", "machine", "x", True))
    assert store.alert_counts(0.0) == {WEB: 2, "machine": 1}
    assert store.alert_counts(2.5) == {WEB: 1, "machine": 1}


def test_latest_crash_skips_dismissed_and_quiet(tmp_path: Path) -> None:
    store = Store(tmp_path / "db")
    loud = ResourceEvent(10.0, "crash", "critical", "wezterm:7", "WezTerm was closed.", True)
    store.record_resource_event(loud)
    store.record_resource_event(ResourceEvent(20.0, "crash", "info", "wezterm:8", "q", False))
    assert store.latest_crash(0.0) == loud
    assert store.latest_crash(11.0) is None
    store.dismiss_report(10.0)
    assert store.latest_crash(0.0) is None


def test_v3_db_upgrades_and_keeps_events(tmp_path: Path) -> None:
    path = tmp_path / "cardak.db"
    Store(path).close()
    con = sqlite3.connect(path)  # back to the v3 layout: no history tables
    con.executescript(
        "DROP TABLE resource_minutes; DROP TABLE dismissed_reports; "
        "INSERT INTO resource_events VALUES (1.0, 'big', 'warning', 'pane:3', 'kept', 1); "
        "PRAGMA user_version = 3;"
    )
    con.close()
    store = Store(path)
    assert [e.text for e in store.resource_events_since(0.0)] == ["kept"]
    store.record_minutes([minute(0, 1)])
    assert len(store.history_days(0.0)) == 1
    store.close()
    con = sqlite3.connect(path)
    assert con.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    con.close()


def test_prune_drops_old_minutes(tmp_path: Path) -> None:
    store = Store(tmp_path / "db")
    store.record_minutes([minute(0, 1), minute(int(29 * DAY // 60), 2)])
    store.record_resource_event(ResourceEvent(0.0, "crash", "critical", "wezterm:1", "x", True))
    store.dismiss_report(0.0)
    store.prune(31 * DAY)
    assert [h[1] for h in store.history_hours(WEB, 0.0)] == [3]
    assert [d.ram_peak for d in store.history_days(-1.0)] == [3]  # daily peaks pruned too
    assert store.latest_crash(-1.0) is None


def test_mark_seen_row_pruned_mid_way(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = Store(tmp_path / "db")
    put(store, SessionState("a", status="waiting", kind="finished", status_since=1.0))
    real_get = store._get  # noqa: SLF001
    calls = []

    def gone_on_second(session_id: str) -> SessionState | None:
        calls.append(session_id)
        return real_get(session_id) if len(calls) == 1 else None

    monkeypatch.setattr(store, "_get", gone_on_second)
    store.mark_seen("a", 9.0)  # no exception
    monkeypatch.undo()
    state = store.get("a")
    assert state is not None and state.status == "waiting"


def test_prune_drops_stale_live_sessions(tmp_path: Path) -> None:
    store = Store(tmp_path / "db")
    now = 100 * DAY
    put(store, SessionState("crashed", status="working", updated_at=now - 8 * DAY))
    put(store, SessionState("quiet", status="idle", updated_at=now - 6 * DAY))
    store.prune(now)
    assert store.get("crashed") is None
    assert store.get("quiet") is not None
    assert [s.session_id for s in store.live_sessions()] == ["quiet"]


def test_stale_session_comes_back_on_its_next_event(tmp_path: Path) -> None:
    from cardak.collectors.hooks import HookEvent
    from cardak.core.agents import apply

    store = Store(tmp_path / "db")
    now = 100 * DAY
    put(store, SessionState("s", status="working", pane_id=3, updated_at=now - 8 * DAY))
    store.prune(now)
    event = HookEvent(name="Stop", session_id="s", last_assistant_message="Done.")
    store.update("s", lambda old: apply(old, event, 3, now + 1))
    state = store.get("s")
    assert state is not None and state.status == "waiting" and state.started_at == now + 1


def test_opening_a_current_db_needs_no_write_lock(tmp_path: Path) -> None:
    Store(tmp_path / "db").close()
    holder = sqlite3.connect(tmp_path / "db", isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")  # another hook or the daemon is mid-write
    try:
        start = time.monotonic()
        Store(tmp_path / "db").close()  # used to wait out the busy timeout and fail
        assert time.monotonic() - start < 1.0
    finally:
        holder.execute("ROLLBACK")
        holder.close()


def test_read_only_current_db_opens(tmp_path: Path) -> None:
    path = tmp_path / "db"
    store = Store(path)
    put(store, SessionState("a"))
    store.close()
    path.chmod(0o444)
    try:
        assert Store(path).get("a") is not None
    finally:
        path.chmod(0o644)


def reply(key: str, ts: float, model: str = "claude-opus-5-5") -> Reply:
    return Reply(key, ts, "s1", "/home/user/webshop", model, 3, 400, 20000, 0, 5000, "standard")


def prompt(key: str, ts: float) -> Prompt:
    return Prompt(key, ts, "s1", "/home/user/webshop", 15)


def test_record_usage_ignores_duplicates(tmp_path: Path) -> None:
    store = Store(tmp_path / "c.db")
    usage = Usage((reply("a:1", 10.0), reply("b:2", 11.0)), (prompt("p1", 9.0),))
    assert store.record_usage("/t/s.jsonl", 7, 100, usage) == 2
    resumed = Usage((reply("a:1", 10.0),), (prompt("p1", 9.0),))  # copied into a resumed file
    assert store.record_usage("/t/r.jsonl", 8, 50, resumed) == 0
    assert [r.key for r in store.replies_since(0.0)] == ["a:1", "b:2"]
    assert store.replies_since(0.0)[0] == reply("a:1", 10.0)
    assert store.prompts_since(0.0) == [prompt("p1", 9.0)]


def test_record_usage_moves_offset(tmp_path: Path) -> None:
    store = Store(tmp_path / "c.db")
    store.record_usage("/t/s.jsonl", 7, 100, Usage())
    store.record_usage("/t/s.jsonl", 7, 250, Usage())
    store.record_usage("/t/r.jsonl", 9, 5, Usage())
    assert store.transcript_offsets() == {"/t/s.jsonl": (7, 250), "/t/r.jsonl": (9, 5)}


def test_replies_since_filters(tmp_path: Path) -> None:
    store = Store(tmp_path / "c.db")
    store.record_usage("/t", 1, 1, Usage((reply("b", 20.0), reply("a", 5.0)), (prompt("p", 4.0),)))
    assert [r.key for r in store.replies_since(10.0)] == ["b"]
    assert [r.key for r in store.replies_since(0.0)] == ["a", "b"]  # oldest first
    assert store.prompts_since(10.0) == []


def test_prune_drops_old_replies(tmp_path: Path) -> None:
    store = Store(tmp_path / "c.db")
    now = 100 * DAY
    old, kept = now - 63 * DAY, now - 61 * DAY
    usage = Usage(
        (reply("old", old), reply("kept", kept)), (prompt("old", old), prompt("kept", kept))
    )
    store.record_usage("/t", 1, 1, usage)
    store.prune(now)
    assert [r.key for r in store.replies_since(0.0)] == ["kept"]
    assert [p.key for p in store.prompts_since(0.0)] == ["kept"]


def test_schema_5_opens_in_place(tmp_path: Path) -> None:
    path = tmp_path / "cardak.db"
    Store(path).close()
    con = sqlite3.connect(path)  # back to the v4 layout: no usage tables
    con.executescript(
        "DROP TABLE claude_replies; DROP TABLE claude_prompts; DROP TABLE transcript_offsets; "
        "INSERT INTO resource_events VALUES (1.0, 'big', 'warning', 'pane:3', 'kept', 1); "
        "PRAGMA user_version = 4;"
    )
    con.close()
    store = Store(path)
    assert [e.text for e in store.resource_events_since(0.0)] == ["kept"]
    assert store.record_usage("/t", 1, 1, Usage((reply("a", 1.0),))) == 1
    store.close()
    con = sqlite3.connect(path)
    assert con.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 6
    assert con.execute("PRAGMA application_id").fetchone()[0] == MIN_READER == 1
    con.close()


def test_sidechain_flag_round_trips(tmp_path: Path) -> None:
    store = Store(tmp_path / "c.db")
    sub = replace(reply("b:2", 11.0), sidechain=True)
    store.record_usage("/t/s.jsonl", 7, 100, Usage((reply("a:1", 10.0), sub), ()))
    assert {r.key: r.sidechain for r in store.replies_since(0.0)} == {"a:1": False, "b:2": True}


def test_flag_sidechains_marks_stored_replies(tmp_path: Path) -> None:
    store = Store(tmp_path / "c.db")
    store.record_usage("/t/s.jsonl", 7, 100, Usage((reply("a:1", 10.0), reply("b:2", 11.0)), ()))
    store.flag_sidechains(["b:2", "zzz"])
    assert {r.key: r.sidechain for r in store.replies_since(0.0)} == {"a:1": False, "b:2": True}
    assert store.usage_mark() == (2, 0)  # flagging stores no reply


def test_backfill_mark(tmp_path: Path) -> None:
    store = Store(tmp_path / "c.db")
    assert not store.backfilled("sidechains")
    store.mark_backfilled("sidechains", 5.0)
    assert Store(tmp_path / "c.db").backfilled("sidechains")


def test_schema_6_keeps_min_reader_1(tmp_path: Path) -> None:
    Store(tmp_path / "c.db")
    con = sqlite3.connect(tmp_path / "c.db")
    assert con.execute("PRAGMA user_version").fetchone()[0] == 6
    assert con.execute("PRAGMA application_id").fetchone()[0] == 1


def test_prune_drops_flags_of_pruned_replies(tmp_path: Path) -> None:
    store = Store(tmp_path / "c.db")
    now = 100 * 86_400.0
    old = replace(reply("old", now - USAGE_KEEP_SECONDS - 1), sidechain=True)
    new = replace(reply("new", now), sidechain=True)
    store.record_usage("/t/s.jsonl", 7, 100, Usage((old, new), ()))
    store.prune(now)
    rows = store._con.execute("SELECT reply_key FROM claude_sidechain_replies").fetchall()
    assert [r[0] for r in rows] == ["new"]
