from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from cardak.collectors.usage import Reply
from cardak.core.context import (
    ContextSeries,
    DayContext,
    SessionLine,
    build_context,
    context_tokens,
    day_context,
    day_points,
    drop_indices,
    group_context,
    position,
)
from cardak.core.resolver import Resolved
from cardak.core.trends import ALL, DAYS, OTHER

TODAY = date(2026, 10, 28)


def at(d: date, hour: float = 12.0) -> float:
    return datetime.combine(d, datetime.min.time()).timestamp() + hour * 3600


NOW = at(TODAY, 20)


def r(
    ts: float,
    ctx: int,
    sid: str = "s1",
    cwd: str | None = "/home/user/webshop",
    sidechain: bool = False,
) -> Reply:
    # context in the cache read: what a long session mostly is
    return Reply(f"{sid}:{ts}", ts, sid, cwd, "claude-opus-5-5", 0, 10, ctx, 0, 0, None, sidechain)


def line(sid: str, *points: tuple[float, int], cwd: str = "/home/user/webshop") -> SessionLine:
    return SessionLine(sid, cwd, tuple(points))


def test_context_tokens_counts_the_whole_input() -> None:
    reply = Reply("k", 0, "s", None, "m", 1, 999, 20, 300, 4000, None)
    assert context_tokens(reply) == 4321  # output tokens are not context


def test_sidechain_and_zero_replies_are_left_out() -> None:
    replies = [
        r(at(TODAY, 9), 120_000),
        r(at(TODAY, 9.1), 30_000, sidechain=True),
        r(at(TODAY, 9.2), 0),
        r(at(TODAY, 9.3), 130_000),
    ]
    series = build_context(replies, TODAY, NOW)
    assert [p[1] for p in series.lines[0].points] == [120_000, 130_000]


def test_replies_without_a_session_or_outside_the_window_are_left_out() -> None:
    old = TODAY - timedelta(days=DAYS)
    replies = [r(at(old), 50_000), r(at(TODAY), 60_000, sid="s2"), r(at(TODAY), 70_000)]
    replies.append(Reply("x", at(TODAY), None, None, "m", 0, 0, 80_000, 0, 0, None))
    series = build_context(replies, TODAY, NOW)
    assert {(ln.session_id, ln.points[0][1]) for ln in series.lines} == {
        ("s1", 70_000),
        ("s2", 60_000),
    }


def test_latest_is_the_last_main_reply() -> None:
    replies = [
        r(at(TODAY, 9), 120_000),
        r(at(TODAY, 10), 140_000),
        r(at(TODAY, 11), 20_000, sidechain=True),
    ]
    assert build_context(replies, TODAY, NOW).latest == {"s1": 140_000}


def test_a_line_keeps_its_first_cwd() -> None:
    replies = [r(at(TODAY, 9), 1000), r(at(TODAY, 10), 2000, cwd="/home/user/webshop/api")]
    assert build_context(replies, TODAY, NOW).lines[0].cwd == "/home/user/webshop"


def test_drop_rule() -> None:
    pts = [(0.0, 300_000), (1.0, 140_000), (2.0, 150_000), (3.0, 90_000), (4.0, 40_000)]
    assert drop_indices(pts) == [1]  # 90k → 40k: from under 100k, not a compaction
    assert drop_indices([(0.0, 100_001), (1.0, 50_000)]) == [1]
    assert drop_indices([(0.0, 100_001), (1.0, 50_001)]) == []
    assert drop_indices([]) == []


def test_day_numbers() -> None:
    compacted = line(
        "a",
        (at(TODAY, 9), 200_000),
        (at(TODAY, 10), 380_000),
        (at(TODAY, 10.1), 90_000),
        (at(TODAY, 12), 410_000),
        (at(TODAY, 12.1), 95_000),
        (at(TODAY, 19.5), 120_000),  # used half an hour ago: not ended
    )
    cleared = line("b", (at(TODAY, 13), 40_000), (at(TODAY, 14), 70_000))
    days = day_context([compacted, cleared], TODAY, NOW)
    assert len(days) == DAYS
    assert days[-1] == DayContext(peak=410_000, sessions=2, drops=2, ended=1, ended_clean=1)
    assert days[-2] is None


def test_recent_session_is_not_ended_until_an_hour_later() -> None:
    recent = line("a", (at(TODAY, 19.5), 40_000))
    assert day_context([recent], TODAY, NOW)[-1] == DayContext(40_000, 1, 0, 0, 0)
    later = at(TODAY, 20.6)
    assert day_context([recent], TODAY, later)[-1] == DayContext(40_000, 1, 0, 1, 1)


def test_compacted_session_that_ended_is_not_clean() -> None:
    done = line("a", (at(TODAY, 9), 300_000), (at(TODAY, 9.5), 80_000))
    assert day_context([done], TODAY, NOW)[-1] == DayContext(300_000, 1, 1, 1, 0)


def test_session_across_midnight() -> None:
    yesterday = TODAY - timedelta(days=1)
    night = line("a", (at(yesterday, 23), 300_000), (at(TODAY, 1), 120_000))
    days = day_context([night], TODAY, NOW)
    assert days[-2] == DayContext(300_000, 1, 0, 0, 0)
    assert days[-1] == DayContext(120_000, 1, 1, 1, 0)  # the drop and the end fall on today


def test_day_points() -> None:
    yesterday = TODAY - timedelta(days=1)
    night = line("a", (at(yesterday, 23), 300_000), (at(TODAY, 1), 120_000))
    assert day_points(night, TODAY) == [(at(TODAY, 1), 120_000)]


PROJECTS = {
    "/home/user/webshop": Resolved("config", Path("/home/user/webshop"), "webshop"),
    "/home/user/api-gw": Resolved("git", Path("/home/user/api-gw"), "api-gw"),
}


def resolve(cwd: str | None) -> Resolved:
    return PROJECTS.get(cwd or "", Resolved("other", None, "Other"))


def test_group_context_rows_order_and_all() -> None:
    lines = (
        line("w", (at(TODAY, 9), 100_000)),
        line("g", (at(TODAY, 9), 200_000), cwd="/home/user/api-gw"),
        line("o", (at(TODAY, 9), 300_000), cwd="/tmp/x"),
    )
    grouped = group_context(ContextSeries(TODAY, NOW, lines, {}), resolve)
    assert [(row.subject, row.name) for row in grouped.rows] == [
        ("project:/home/user/api-gw", "api-gw"),
        ("project:/home/user/webshop", "webshop"),
        (OTHER, "Other"),
        (ALL, "All projects"),
    ]
    assert [ln.session_id for ln in grouped.rows[-1].lines] == ["w", "g", "o"]
    days = grouped.rows[-1].days[-1]
    assert days is not None and days.peak == 300_000 and days.sessions == 3


def test_group_context_empty() -> None:
    assert group_context(ContextSeries(TODAY, NOW, (), {}), resolve).rows == ()


def test_position() -> None:
    assert position(10_000) == 0 and position(1_000_000) == 1
    assert position(100_000) == pytest.approx(0.5)
    assert position(5) == 0 and position(5_000_000) == 1
    assert position(0) == 0
