import sqlite3
import threading
import time
from pathlib import Path

import pytest

from cardak.core.agents import SessionState
from cardak.core.config import state_dir
from cardak.core.store import SCHEMA_VERSION, Store, StoreError, SummaryRow, db_path

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
    assert con.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 2
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


def test_newer_schema_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "db"
    Store(path).close()
    con = sqlite3.connect(path)
    con.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    con.close()
    with pytest.raises(StoreError):
        Store(path)


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
    assert SCHEMA_VERSION == 2


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
