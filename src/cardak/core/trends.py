"""Claude Code usage day by day (#88): per folder, then per project, over the history window.

`build_trends` sums the stored replies and prompts per (cwd, local day); `group_trends` folds
folders into projects once the snapshot knows which project each folder belongs to. Pure.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from cardak.collectors.prices import ModelPrice
from cardak.collectors.usage import Prompt, Reply
from cardak.core.prices import lookup, priced, reply_cost

DAYS = 30  # the history window, RAM/CPU and usage alike
OTHER = "other"
PROJECT_KINDS = ("config", "git")


@dataclass(frozen=True)
class DayUsage:
    cost: float = 0.0  # USD, priced replies only
    unpriced: int = 0  # replies with no price (or fast mode)
    tokens: int = 0  # input + output + cache read + both cache writes
    output_tokens: int = 0
    replies: int = 0
    prompts: int = 0
    prompt_chars: int = 0
    sessions: frozenset[str] = frozenset()


def merge(a: DayUsage, b: DayUsage) -> DayUsage:
    return DayUsage(
        a.cost + b.cost,
        a.unpriced + b.unpriced,
        a.tokens + b.tokens,
        a.output_tokens + b.output_tokens,
        a.replies + b.replies,
        a.prompts + b.prompts,
        a.prompt_chars + b.prompt_chars,
        a.sessions | b.sessions,
    )


@dataclass(frozen=True)
class UsageTrends:
    today: date
    cells: Mapping[tuple[str | None, int], DayUsage]  # (cwd, day index, 0 = oldest)
    unpriced: tuple[str, ...]  # as MonthSpend.unpriced


def first_day(today: date) -> date:
    return today - timedelta(days=DAYS - 1)


def window_start(today: date) -> float:
    """Local midnight at the start of the window's first day."""
    return datetime.combine(first_day(today), datetime.min.time()).timestamp()


class _Cell:
    def __init__(self) -> None:
        self.cost = 0.0
        self.unpriced = self.tokens = self.output_tokens = self.replies = 0
        self.prompts = self.prompt_chars = 0
        self.sessions: set[str] = set()

    def freeze(self) -> DayUsage:
        return DayUsage(
            self.cost,
            self.unpriced,
            self.tokens,
            self.output_tokens,
            self.replies,
            self.prompts,
            self.prompt_chars,
            frozenset(self.sessions),
        )


def build_trends(
    replies: Iterable[Reply],
    prompts: Iterable[Prompt],
    prices: Mapping[str, ModelPrice],
    today: date,
) -> UsageTrends:
    first = first_day(today)
    cells: dict[tuple[str | None, int], _Cell] = {}
    unpriced: set[str] = set()

    def cell(ts: float, cwd: str | None) -> _Cell | None:
        index = (date.fromtimestamp(ts) - first).days
        return cells.setdefault((cwd, index), _Cell()) if 0 <= index < DAYS else None

    for r in replies:
        if (c := cell(r.ts, r.cwd)) is None:
            continue
        c.replies += 1
        c.output_tokens += r.output_tokens
        c.tokens += r.input_tokens + r.output_tokens + r.cache_read_tokens
        c.tokens += r.cache_write_5m_tokens + r.cache_write_1h_tokens
        if r.session_id is not None:
            c.sessions.add(r.session_id)
        price = lookup(prices, r.model)
        if price is None or not priced(r):
            c.unpriced += 1
            unpriced.add(r.model if price is None else f"{r.model} (fast mode)")
        else:
            c.cost += reply_cost(price, r)
    for p in prompts:
        if (c := cell(p.ts, p.cwd)) is not None:
            c.prompts += 1
            c.prompt_chars += p.chars
    frozen = {k: c.freeze() for k, c in cells.items()}
    return UsageTrends(today, frozen, tuple(sorted(unpriced)))
