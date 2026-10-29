"""Claude Code usage day by day (#88): per folder, then per project, over the history window.

`build_trends` sums the stored replies and prompts per (cwd, local day); `group_trends` folds
folders into projects once the snapshot knows which project each folder belongs to. Pure.
"""

import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import reduce
from typing import Literal

from cardak.collectors.prices import ModelPrice
from cardak.collectors.usage import Prompt, Reply
from cardak.core.prices import lookup, priced, reply_cost
from cardak.core.resolver import Resolved

DAYS = 30  # the history window, RAM/CPU and usage alike
OTHER = "other"
PROJECT_KINDS = ("config", "git")
ALL = "all"  # the All projects row

Figure = Literal["cost", "tokens", "prompts", "sessions", "prompt_len", "reply_len", "per_session"]
FIGURES: tuple[Figure, ...] = (
    "cost",
    "tokens",
    "prompts",
    "sessions",
    "prompt_len",
    "reply_len",
    "per_session",
)
AVERAGED: tuple[Figure, ...] = ("prompt_len", "reply_len", "per_session")
LABELS: Mapping[Figure, str] = {
    "cost": "cost",
    "tokens": "tokens",
    "prompts": "prompts",
    "sessions": "sessions",
    "prompt_len": "prompt length",
    "reply_len": "reply length",
    "per_session": "tokens per session",
}


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


@dataclass(frozen=True)
class ProjectTrend:
    subject: str  # "project:<key>" as in the RAM history, OTHER or ALL
    name: str
    days: tuple[DayUsage | None, ...]  # DAYS cells, oldest first; None: no use that day


@dataclass(frozen=True)
class GroupedTrends:
    today: date
    rows: tuple[ProjectTrend, ...]  # projects A–Z, then Other, then All projects
    unpriced: tuple[str, ...]


def _fold(cells: Mapping[int, DayUsage]) -> tuple[DayUsage | None, ...]:
    return tuple(cells.get(i) for i in range(DAYS))


def group_trends(trends: UsageTrends, resolve: Callable[[str | None], Resolved]) -> GroupedTrends:
    """Each folder's days under the project it belongs to, plus All projects for everything."""
    names: dict[str, str] = {}
    days: dict[str, dict[int, DayUsage]] = {}
    every: dict[int, DayUsage] = {}
    for (cwd, index), d in trends.cells.items():
        r = resolve(cwd)
        subject = f"project:{r.key}" if r.kind in PROJECT_KINDS else OTHER
        names.setdefault(subject, r.name if subject != OTHER else "Other")
        row = days.setdefault(subject, {})
        row[index] = merge(row[index], d) if index in row else d
        every[index] = merge(every[index], d) if index in every else d
    order = sorted(names, key=lambda s: (s == OTHER, names[s].lower()))
    rows = [ProjectTrend(s, names[s], _fold(days[s])) for s in order]
    if rows:
        rows.append(ProjectTrend(ALL, "All projects", _fold(every)))
    return GroupedTrends(trends.today, tuple(rows), trends.unpriced)


def _ratio(n: float, d: int) -> float | None:
    return n / d if d else None


def value(d: DayUsage | None, figure: Figure) -> float | None:
    """One figure of one day (or of a merged window); None: no use, or nothing to average."""
    if d is None:
        return None
    match figure:
        case "cost":
            return d.cost
        case "tokens":
            return float(d.tokens)
        case "prompts":
            return float(d.prompts)
        case "sessions":
            return float(len(d.sessions))
        case "prompt_len":
            return _ratio(d.prompt_chars, d.prompts)
        case "reply_len":
            return _ratio(d.output_tokens, d.replies)
        case "per_session":
            return _ratio(d.tokens, len(d.sessions))


@dataclass(frozen=True)
class UsageRow:
    subject: str
    name: str
    today: float | None
    per_day: float | None  # mean over the days with use
    peak: float | None
    window: float | None  # the whole window: sums, distinct sessions, weighted averages
    days: tuple[float | None, ...]
    incomplete: bool  # cost only: some reply in the window had no price
    incomplete_today: bool


def _mean_peak(cells: Sequence[float | None]) -> tuple[float | None, float | None]:
    seen = [v for v in cells if v is not None]
    return (sum(seen) / len(seen), max(seen)) if seen else (None, None)


def _row(t: ProjectTrend, figure: Figure) -> UsageRow | None:
    used = [d for d in t.days if d is not None]
    if not used:
        return None
    cells = tuple(value(d, figure) for d in t.days)
    per_day, peak = _mean_peak(cells)
    last, cost = t.days[-1], figure == "cost"
    return UsageRow(
        subject=t.subject,
        name=t.name,
        today=cells[-1],
        per_day=per_day,
        peak=peak,
        window=value(reduce(merge, used), figure),
        days=cells,
        incomplete=cost and any(d.unpriced for d in used),
        incomplete_today=cost and last is not None and last.unpriced > 0,
    )


def usage_rows(grouped: GroupedTrends, figure: Figure) -> list[UsageRow]:
    """One row per project with use in the window, in the grouped order."""
    return [r for t in grouped.rows if (r := _row(t, figure)) is not None]


def usage_edges(rows: Sequence[UsageRow]) -> tuple[float, float, float, float]:
    """Band edges at ¼, ½, ¾ and all of the busiest day cell in these rows."""
    top = max((v for r in rows for v in r.days if v is not None), default=0.0)
    if top <= 0:
        return (math.inf, math.inf, math.inf, math.inf)
    return (top / 4, top / 2, top * 3 / 4, top)
