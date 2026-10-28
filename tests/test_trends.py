from datetime import date, datetime, timedelta

import pytest

from cardak.collectors.usage import Prompt, Reply
from cardak.core.prices import BUILTIN, reply_cost
from cardak.core.trends import DayUsage, build_trends, merge

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
    return Reply(key or f"m{ts}:{cwd}:{inp}", ts, session, cwd, model, inp, out, read, w5, w1, speed)


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
