"""30 days per project: the daemon's minute rollups and the history view's rows. Pure."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from cardak.core.resources import GIB, Thresholds
from cardak.core.snapshot import DeckSnapshot
from cardak.core.store import DayPeak, MinuteRow
from cardak.core.trends import DAYS, OTHER, PROJECT_KINDS

Metric = Literal["ram", "cpu"]
SORTS = ("peak", "now", "median", "alerts", "name")
CELLS = "░▒▓▓█"


def _live(snap: DeckSnapshot) -> list[tuple[str, str, int, float]]:
    """(subject, name, RAM, CPU) for every project and Other in one snapshot."""
    out: list[tuple[str, str, int, float]] = []
    other = None
    for g in snap.groups:
        if g.kind in PROJECT_KINDS:
            out.append((f"project:{g.key}", g.name, g.rss, g.cpu_pct))
        elif g.kind == "other":
            other = (OTHER, "Other", g.rss, g.cpu_pct)
    m = snap.machine
    if other is None and m is not None:  # WezTerm unreachable: no Other group, all of it is Other
        rss = max(0, m.mem_used - sum(x[2] for x in out))
        cpu = max(0.0, (m.cpu_pct or 0.0) * m.n_cpus - sum(x[3] for x in out))
        other = (OTHER, "Other", rss, cpu)
    return [*out, *([other] if other else [])]


class Rollup:
    """Accumulates 2-second snapshots; hands back each finished minute's rows."""

    def __init__(self) -> None:
        self._minute: int | None = None
        self._acc: dict[str, tuple[str, list[int], list[float]]] = {}

    def add(self, snap: DeckSnapshot, now: float) -> list[MinuteRow]:
        if snap.machine is None:
            return []
        minute = int(now // 60)
        done = self.flush() if self._minute is not None and minute != self._minute else []
        self._minute = minute
        for subject, name, rss, cpu in _live(snap):
            _, rams, cpus = self._acc.setdefault(subject, (name, [], []))
            rams.append(rss)
            cpus.append(cpu)
        return done

    def flush(self) -> list[MinuteRow]:
        """The current (partial) minute's rows, and start over."""
        minute, acc = self._minute, self._acc
        self._minute, self._acc = None, {}
        if minute is None:
            return []
        return [
            MinuteRow(
                minute,
                subject,
                name,
                sum(rams) // len(rams),
                max(rams),
                sum(cpus) / len(cpus),
                max(cpus),
            )  # fmt: skip
            for subject, (name, rams, cpus) in acc.items()
        ]


@dataclass(frozen=True)
class HistoryRow:
    subject: str
    name: str
    now: float | None
    median: float | None
    peak: float | None
    alerts: int | None  # None for Other: it never alerts
    days: tuple[float | None, ...]  # oldest first; the last cell is today


def _day_cells(
    days: Sequence[DayPeak], first: date, metric: Metric
) -> tuple[dict[str, str], dict[str, list[float | None]]]:
    """Stored names, and each subject's DAYS cells of daily peaks (None: no data that day)."""
    names: dict[str, str] = {}
    cells: dict[str, list[float | None]] = {}
    for d in days:
        names.setdefault(d.subject, d.name)
        row = cells.setdefault(d.subject, [None] * DAYS)
        index = (date.fromisoformat(d.day) - first).days
        if 0 <= index < DAYS:
            row[index] = float(d.ram_peak if metric == "ram" else d.cpu_peak)
    return names, cells


def history_rows(
    days: Sequence[DayPeak],
    medians: Mapping[str, tuple[float, float]],
    counts: Mapping[str, int],
    live: DeckSnapshot | None,
    today: date,
    metric: Metric,
) -> list[HistoryRow]:
    names, cells = _day_cells(days, today - timedelta(days=DAYS - 1), metric)
    now: dict[str, float] = {}
    for subject, name, rss, cpu in _live(live) if live is not None else []:
        names[subject] = name
        now[subject] = float(rss if metric == "ram" else cpu)
    pick = 0 if metric == "ram" else 1
    out: list[HistoryRow] = []
    for subject in sorted(names, key=lambda s: (s == OTHER, names[s].lower())):
        values = cells.get(subject, [None] * DAYS)
        seen = [v for v in values if v is not None]
        median = medians.get(subject)
        out.append(
            HistoryRow(
                subject=subject,
                name=names[subject],
                now=now.get(subject),
                median=median[pick] if median else None,
                peak=max(seen) if seen else None,
                alerts=None if subject == OTHER else counts.get(subject, 0),
                days=tuple(values),
            )
        )
    return out


def sort_rows(rows: Sequence[HistoryRow], key: str) -> list[HistoryRow]:
    """Biggest first (None last), names A–Z; Other always last."""
    if key == "name":
        ordered = sorted(rows, key=lambda r: r.name.lower())
    else:
        ordered = sorted(rows, key=lambda r: (getattr(r, key) is None, -(getattr(r, key) or 0)))
    return [r for r in ordered if r.subject != OTHER] + [r for r in ordered if r.subject == OTHER]


def edges(t: Thresholds, metric: Metric) -> tuple[float, float, float, float]:
    """Band edges at ¼, ½, ¾ and all of the pane threshold, so █ means "would have alerted"."""
    top = float(t.pane_ram if metric == "ram" else t.pane_cpu_pct)
    return (top / 4, top / 2, top * 3 / 4, top)


def band(value: float | None, e: Sequence[float]) -> int:
    """-1 for a day without data, else 0–4."""
    if value is None:
        return -1
    return sum(value >= edge for edge in e)


def _fmt(value: float, metric: Metric) -> str:
    return f"{value / GIB:.1f}" if metric == "ram" else f"{value:.0f}"


def legend(e: Sequence[float], metric: Metric) -> str:
    unit = " GB" if metric == "ram" else "%"
    parts = [f"{CELLS[0]} <{_fmt(e[0], metric)}{unit}"]
    parts += [f"{CELLS[i]} {_fmt(e[i - 1], metric)}–{_fmt(e[i], metric)}" for i in range(1, len(e))]
    parts.append(f"{CELLS[-1]} {_fmt(e[-1], metric)}+")
    return f"peak {metric.upper()} per day:  " + "  ".join(parts)
