"""Each session's context over the history window (#86), from the stored replies.

A reply's context is the request's whole input. Only the main thread counts: a subagent's replies
are filed under its parent's session with a context of their own. Compactions and clears are not
recorded anywhere; they show in the data: a compaction is a sharp drop inside one session
(`drop_indices`), a clear a session's line ending. Pure.
"""

import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from cardak.collectors.usage import Reply
from cardak.core.resolver import Resolved
from cardak.core.trends import ALL, DAYS, OTHER, PROJECT_KINDS, first_day, window_start

DROP_FROM = 100_000  # a compaction: from over this...
DROP_RATIO = 0.5  # ...to under half of it (29 of 30 matched Claude Code's own record)
ENDED_AFTER_S = 3600  # a session's last reply this old: it ended
LOW, HIGH = 10_000, 1_000_000  # the log scale's ends
TICKS = (10_000, 100_000, 1_000_000)
EDGES = (50_000, 100_000, 200_000, 400_000)  # the history table's bands

Point = tuple[float, int]  # (ts, context)


def context_tokens(r: Reply) -> int:
    return r.input_tokens + r.cache_read_tokens + r.cache_write_5m_tokens + r.cache_write_1h_tokens


@dataclass(frozen=True)
class SessionLine:
    session_id: str
    cwd: str | None  # its first reply's
    points: tuple[Point, ...]  # oldest first


@dataclass(frozen=True)
class ContextSeries:
    today: date
    built_at: float
    lines: tuple[SessionLine, ...]  # in order of first reply
    latest: Mapping[str, int]  # session id -> its last reply's context


def build_context(replies: Iterable[Reply], today: date, now: float) -> ContextSeries:
    start = window_start(today)
    cwds: dict[str, str | None] = {}
    points: dict[str, list[Point]] = {}
    for r in replies:
        if r.sidechain or r.session_id is None or r.ts < start:
            continue
        if (ctx := context_tokens(r)) <= 0:  # an error reply reports nothing
            continue
        cwds.setdefault(r.session_id, r.cwd)
        points.setdefault(r.session_id, []).append((r.ts, ctx))
    lines = tuple(SessionLine(sid, cwds[sid], tuple(sorted(p))) for sid, p in points.items())
    return ContextSeries(today, now, lines, {ln.session_id: ln.points[-1][1] for ln in lines})


def drop_indices(points: Sequence[Point]) -> list[int]:
    return [
        i
        for i in range(1, len(points))
        if points[i - 1][1] > DROP_FROM and points[i][1] < points[i - 1][1] * DROP_RATIO
    ]


@dataclass(frozen=True)
class DayContext:
    peak: int
    sessions: int  # with a reply that day
    drops: int
    ended: int  # sessions whose last reply was that day, an hour or more ago
    ended_clean: int  # of those, the ones that never dropped


class _Day:
    def __init__(self) -> None:
        self.peak = self.drops = self.ended = self.ended_clean = 0
        self.sessions: set[str] = set()

    def freeze(self) -> DayContext:
        return DayContext(self.peak, len(self.sessions), self.drops, self.ended, self.ended_clean)


def day_context(
    lines: Sequence[SessionLine], today: date, now: float
) -> tuple[DayContext | None, ...]:
    """`DAYS` cells, oldest first; None for a day no session touched."""
    first = first_day(today)
    days: dict[int, _Day] = {}

    def day(ts: float) -> _Day | None:
        index = (date.fromtimestamp(ts) - first).days
        return days.setdefault(index, _Day()) if 0 <= index < DAYS else None

    for ln in lines:
        for ts, ctx in ln.points:
            if (d := day(ts)) is not None:
                d.peak = max(d.peak, ctx)
                d.sessions.add(ln.session_id)
        drops = drop_indices(ln.points)
        for i in drops:
            if (d := day(ln.points[i][0])) is not None:
                d.drops += 1
        last = ln.points[-1][0]
        if now - last >= ENDED_AFTER_S and (d := day(last)) is not None:
            d.ended += 1
            d.ended_clean += not drops
    return tuple(days[i].freeze() if i in days else None for i in range(DAYS))


def day_points(line: SessionLine, day: date) -> list[Point]:
    start = datetime.combine(day, datetime.min.time()).timestamp()
    end = datetime.combine(day + timedelta(days=1), datetime.min.time()).timestamp()
    return [p for p in line.points if start <= p[0] < end]


@dataclass(frozen=True)
class ProjectContext:
    subject: str  # "project:<key>" as in the RAM history, OTHER or ALL
    name: str
    lines: tuple[SessionLine, ...]
    days: tuple[DayContext | None, ...]


@dataclass(frozen=True)
class GroupedContext:
    today: date
    built_at: float
    rows: tuple[ProjectContext, ...]  # projects A–Z, then Other, then All projects


def group_context(
    series: ContextSeries, resolve: Callable[[str | None], Resolved]
) -> GroupedContext:
    """Each session under the project of its cwd, plus All projects for every session."""
    names: dict[str, str] = {}
    lines: dict[str, list[SessionLine]] = {}
    for ln in series.lines:
        r = resolve(ln.cwd)
        subject = f"project:{r.key}" if r.kind in PROJECT_KINDS else OTHER
        names.setdefault(subject, r.name if subject != OTHER else "Other")
        lines.setdefault(subject, []).append(ln)
    order = sorted(names, key=lambda s: (s == OTHER, names[s].lower()))

    def row(subject: str, name: str, found: Sequence[SessionLine]) -> ProjectContext:
        days = day_context(found, series.today, series.built_at)
        return ProjectContext(subject, name, tuple(found), days)

    rows = [row(s, names[s], lines[s]) for s in order]
    if rows:
        rows.append(row(ALL, "All projects", series.lines))
    return GroupedContext(series.today, series.built_at, tuple(rows))


def position(v: float) -> float:
    """Where a context sits on the log scale: 0 at `LOW` and below, 1 at `HIGH` and above."""
    if v <= LOW:
        return 0.0
    return min(math.log10(v / LOW) / math.log10(HIGH / LOW), 1.0)


@dataclass(frozen=True)
class ContextRow:
    """A history-table row: named as `trends.UsageRow` is, so both sort the same way."""

    subject: str
    name: str
    today: int | None  # today's peak
    per_day: float | None  # mean of the daily peaks
    peak: int | None
    window: int  # drops over the window
    days: tuple[int | None, ...]  # each day's peak


def context_rows(grouped: GroupedContext) -> list[ContextRow]:
    """One row per project with a session in the window, in the grouped order."""
    rows = []
    for p in grouped.rows:
        peaks = tuple(d.peak if d is not None else None for d in p.days)
        seen = [v for v in peaks if v is not None]
        if not seen:
            continue
        drops = sum(d.drops for d in p.days if d is not None)
        rows.append(
            ContextRow(p.subject, p.name, peaks[-1], sum(seen) / len(seen), max(seen), drops, peaks)
        )
    return rows
