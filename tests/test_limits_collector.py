import json
from pathlib import Path

from cardak.collectors.limits import Reading, Window, parse_statusline

FIXTURES = Path(__file__).parent / "fixtures" / "statusline"


def _with(limits: object) -> bytes:
    data = json.loads((FIXTURES / "no_limits.json").read_text())
    data["rate_limits"] = limits
    return json.dumps(data).encode()


def test_parses_both_windows() -> None:
    assert parse_statusline((FIXTURES / "plan.json").read_bytes()) == Reading(
        Window(73.0, 1793370000), Window(39.0, 1793700000)
    )


def test_no_rate_limits_is_none() -> None:
    assert parse_statusline((FIXTURES / "no_limits.json").read_bytes()) is None
    assert parse_statusline(_with(None)) is None


def test_garbage_is_none() -> None:
    assert parse_statusline(b"\xff{") is None
    assert parse_statusline(b"[1, 2]") is None


def test_one_window_missing() -> None:
    reading = parse_statusline(_with({"seven_day": {"used_percentage": 5, "resets_at": 10}}))
    assert reading == Reading(None, Window(5.0, 10))


def test_bad_window_values_dropped() -> None:
    bad = {
        "five_hour": {"used_percentage": "x", "resets_at": 10},
        "seven_day": {"used_percentage": 5},
    }
    assert parse_statusline(_with(bad)) is None
    flag = {"five_hour": {"used_percentage": True, "resets_at": 10}}
    assert parse_statusline(_with(flag)) is None
    assert parse_statusline(_with({"five_hour": {"used_percentage": -1, "resets_at": 10}})) is None
    over = parse_statusline(_with({"five_hour": {"used_percentage": 140, "resets_at": 10}}))
    assert over == Reading(Window(100.0, 10), None)
