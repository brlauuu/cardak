from pathlib import Path

from cardak.collectors.limits import Reading, Window
from cardak.core.limits import (
    MIN_WRITE_S,
    LimitView,
    Stored,
    limits_path,
    limits_view,
    load_limits,
    merge,
    needs_write,
    record,
)

FIVE, WEEK = Window(73.0, 1000.0), Window(39.0, 5000.0)
OLD = Stored(100.0, FIVE, WEEK)


def test_limits_path_is_in_state_dir(tmp_path: Path) -> None:
    assert limits_path({"XDG_STATE_HOME": str(tmp_path)}) == tmp_path / "cardak" / "limits.json"


def test_merge_keeps_higher_within_window() -> None:
    merged = merge(OLD, Reading(Window(60.0, 1000.0), None), 200.0)
    assert merged == OLD  # an idle session repeating an older figure changes nothing


def test_merge_same_figure_refreshes_read_at() -> None:
    assert merge(OLD, Reading(FIVE, None), 200.0) == Stored(200.0, FIVE, WEEK)


def test_merge_new_window_replaces() -> None:
    merged = merge(OLD, Reading(Window(5.0, 1500.0), None), 200.0)
    assert merged == Stored(200.0, Window(5.0, 1500.0), WEEK)


def test_merge_ignores_older_window() -> None:
    assert merge(OLD, Reading(Window(90.0, 900.0), None), 200.0) == OLD


def test_merge_keeps_missing_window() -> None:
    merged = merge(OLD, Reading(None, Window(40.0, 5000.0)), 200.0)
    assert merged == Stored(200.0, FIVE, Window(40.0, 5000.0))


def test_merge_from_nothing() -> None:
    assert merge(None, Reading(FIVE, None), 7.0) == Stored(7.0, FIVE, None)


def test_needs_write_throttles() -> None:
    assert needs_write(None, OLD, 100.0)
    assert not needs_write(OLD, Stored(130.0, FIVE, WEEK), 130.0)
    assert needs_write(OLD, Stored(100.0 + MIN_WRITE_S + 1, FIVE, WEEK), 100.0 + MIN_WRITE_S + 1)
    assert needs_write(OLD, Stored(130.0, Window(80.0, 1000.0), WEEK), 130.0)
    assert not needs_write(OLD, OLD, 500.0)  # nothing new: read_at stays, no write


def test_record_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "state" / "limits.json"
    record(path, Reading(FIVE, WEEK), 100.0)
    assert load_limits(path) == OLD


def test_record_two_writers_keep_the_higher(tmp_path: Path) -> None:
    path = tmp_path / "limits.json"
    record(path, Reading(FIVE, WEEK), 100.0)
    record(path, Reading(Window(50.0, 1000.0), None), 300.0)
    assert load_limits(path) == OLD


def test_load_ignores_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / "limits.json"
    assert load_limits(path) is None
    path.write_bytes(b"{")
    assert load_limits(path) is None
    path.write_text('{"read_at": 1, "five_hour": {"used": "x"}}')
    assert load_limits(path) is None
    path.write_bytes(b"{")
    record(path, Reading(FIVE, None), 100.0)
    assert load_limits(path) == Stored(100.0, FIVE, None)


def test_written_file_is_private(tmp_path: Path) -> None:
    path = tmp_path / "limits.json"
    record(path, Reading(FIVE, WEEK), 100.0)
    assert path.stat().st_mode & 0o777 == 0o600


def test_window_past_reset_is_unknown() -> None:
    view = limits_view(OLD, 1000.0)
    assert view.five_hour == LimitView(None, None, "unknown")
    assert view.seven_day == LimitView(61, 5000.0, "ok")
    assert view.age_s == 900.0
    assert limits_view(Stored(1.0, None, None), 2.0).five_hour.level == "unknown"


def test_levels() -> None:
    def level(used: float) -> str:
        return limits_view(Stored(0.0, Window(used, 10.0), None), 1.0).five_hour.level

    assert level(80.0) == "ok"  # 20 left
    assert level(81.0) == "low"  # 19 left
    assert level(91.0) == "critical"  # 9 left
    assert level(100.0) == "critical"
