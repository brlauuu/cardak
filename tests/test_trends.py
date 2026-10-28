from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from cardak.collectors.usage import Prompt, Reply
from cardak.core.prices import BUILTIN, reply_cost
from cardak.core.resolver import OTHER as OTHER_RESOLVED
from cardak.core.resolver import Resolved
from cardak.core.trends import (
    ALL,
    DAYS,
    DayUsage,
    GroupedTrends,
    ProjectTrend,
    build_trends,
    group_trends,
    merge,
    usage_edges,
    usage_rows,
    value,
)

TODAY = date(2026, 10, 28)
CWD = "/home/user/webshop"


def at(d: date, hour: int = 12, minute: int = 0) -> float:
    return datetime(d.year, d.month, d.day, hour, minute).timestamp()


def _reply(
    ts: float,
    cwd: str | None = CWD,
    session: str | None = "s1",
    model: str = "claude-opus-5-5",
    inp: int = 10,
    out: int = 20,
    read: int = 30,
    w5: int = 40,
    w1: int = 50,
    speed: str | None = "standard",
    key: str | None = None,
) -> Reply:
    key = key or f"m{ts}:{cwd}:{inp}"
    return Reply(key, ts, session, cwd, model, inp, out, read, w5, w1, speed)


def _prompt(ts: float, chars: int = 40, cwd: str | None = CWD, session: str = "s1") -> Prompt:
    return Prompt(f"p{ts}:{chars}", ts, session, cwd, chars)


def test_sums_per_cwd_and_day() -> None:
    replies = [_reply(at(TODAY, 9)), _reply(at(TODAY, 10))]
    t = build_trends(replies, [_prompt(at(TODAY, 9))], BUILTIN, TODAY)
    cell = t.cells[(CWD, 29)]
    assert cell.replies == 2
    assert cell.output_tokens == 40
    assert cell.prompts == 1
    assert cell.prompt_chars == 40
    assert cell.tokens == 2 * (10 + 20 + 30 + 40 + 50)
    assert cell.sessions == frozenset({"s1"})
    assert t.today == TODAY


def test_days_are_local_dates() -> None:
    yesterday = TODAY - timedelta(days=1)
    t = build_trends([_reply(at(yesterday, 23, 59)), _reply(at(TODAY, 0, 1))], [], BUILTIN, TODAY)
    assert set(t.cells) == {(CWD, 28), (CWD, 29)}


def test_rows_outside_the_window_are_ignored() -> None:
    old = TODAY - timedelta(days=30)
    t = build_trends([_reply(at(old))], [_prompt(at(old))], BUILTIN, TODAY)
    assert t.cells == {}


def test_cost_uses_the_tier_per_reply() -> None:
    price = BUILTIN["claude-haiku-5-5"]
    over = _reply(at(TODAY), model="claude-haiku-5-5", inp=150_000)
    under = _reply(at(TODAY), model="claude-haiku-5-5", inp=1_000)
    t = build_trends([over, under], [], BUILTIN, TODAY)
    expected = reply_cost(price, over) + reply_cost(price, under)
    assert t.cells[(CWD, 29)].cost == pytest.approx(expected)


def test_unpriced_and_fast_mode() -> None:
    unknown = _reply(at(TODAY), model="claude-unknown-1")
    fast = _reply(at(TODAY), speed="fast", inp=11)
    t = build_trends([unknown, fast], [], BUILTIN, TODAY)
    cell = t.cells[(CWD, 29)]
    assert cell.cost == 0
    assert cell.unpriced == 2
    assert cell.tokens == (10 + 20 + 30 + 40 + 50) + (11 + 20 + 30 + 40 + 50)
    assert t.unpriced == ("claude-opus-5-5 (fast mode)", "claude-unknown-1")


def test_merge_adds_and_unions() -> None:
    a = DayUsage(1.0, 1, 10, 5, 1, 2, 30, frozenset({"s1"}))
    b = DayUsage(2.0, 0, 20, 6, 2, 1, 10, frozenset({"s2", "s1"}))
    assert merge(a, b) == DayUsage(3.0, 1, 30, 11, 3, 3, 40, frozenset({"s1", "s2"}))


PROJECTS = {
    "/home/user/webshop": Resolved("config", Path("/home/user/webshop"), "webshop"),
    "/home/user/webshop/api": Resolved("config", Path("/home/user/webshop"), "webshop"),
    "/home/user/api-gw": Resolved("git", Path("/home/user/api-gw"), "api-gw"),
}


def resolve(cwd: str | None) -> Resolved:
    return PROJECTS.get(cwd or "", OTHER_RESOLVED)


def test_group_by_project() -> None:
    replies = [
        _reply(at(TODAY), cwd="/home/user/webshop"),
        _reply(at(TODAY), cwd="/home/user/webshop/api"),
        _reply(at(TODAY), cwd="/tmp/x"),
    ]
    g = group_trends(build_trends(replies, [], BUILTIN, TODAY), resolve)
    assert [r.name for r in g.rows] == ["webshop", "Other", "All projects"]
    assert [r.subject for r in g.rows] == ["project:/home/user/webshop", "other", ALL]
    webshop = g.rows[0].days[DAYS - 1]
    assert webshop is not None and webshop.replies == 2
    assert g.today == TODAY


def test_sessions_are_counted_once_across_cwds() -> None:
    replies = [
        _reply(at(TODAY), cwd="/home/user/webshop"),
        _reply(at(TODAY), cwd="/home/user/webshop/api"),
        _reply(at(TODAY), cwd="/home/user/api-gw"),
    ]
    g = group_trends(build_trends(replies, [], BUILTIN, TODAY), resolve)
    assert [value(r.days[DAYS - 1], "sessions") for r in g.rows] == [1, 1, 1]
    assert [r.name for r in g.rows] == ["api-gw", "webshop", "All projects"]


def test_averages_blank_without_a_denominator() -> None:
    d = DayUsage(replies=2, output_tokens=30)
    assert value(d, "prompt_len") is None
    assert value(d, "per_session") is None
    assert value(d, "reply_len") == 15
    assert value(None, "cost") is None


def _grouped(*days: DayUsage | None) -> GroupedTrends:
    cells = (None,) * (DAYS - len(days)) + days
    return GroupedTrends(TODAY, (ProjectTrend("project:/w", "webshop", cells),), ())


def test_usage_rows_columns() -> None:
    g = _grouped(DayUsage(prompts=2), None, DayUsage(prompts=4))
    (row,) = usage_rows(g, "prompts")
    assert (row.per_day, row.peak, row.window, row.today) == (3, 4, 6, 4)
    assert row.days[-2] is None


def test_window_averages_are_weighted() -> None:
    g = _grouped(DayUsage(prompts=1, prompt_chars=10), DayUsage(prompts=3, prompt_chars=90))
    (row,) = usage_rows(g, "prompt_len")
    assert row.window == 25.0
    assert row.per_day == 20.0


def test_window_sessions_are_distinct() -> None:
    day = DayUsage(tokens=100, sessions=frozenset({"s1"}))
    (row,) = usage_rows(_grouped(day, day, day), "sessions")
    assert row.window == 1
    (per,) = usage_rows(_grouped(day, day, day), "per_session")
    assert per.window == 300


def test_incomplete_cost() -> None:
    g = _grouped(DayUsage(cost=1.0, replies=2, unpriced=1))
    (cost,) = usage_rows(g, "cost")
    assert cost.incomplete and cost.incomplete_today
    (tokens,) = usage_rows(g, "tokens")
    assert not tokens.incomplete and not tokens.incomplete_today


def test_usage_edges() -> None:
    rows = usage_rows(_grouped(DayUsage(prompts=8), DayUsage(prompts=2)), "prompts")
    assert usage_edges(rows) == (2.0, 4.0, 6.0, 8.0)
    empty = usage_rows(_grouped(DayUsage(replies=1)), "prompts")
    assert usage_edges(empty) == (float("inf"),) * 4
