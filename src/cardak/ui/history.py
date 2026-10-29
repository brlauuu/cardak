"""30 days per project: the history table (`h`) and one project's day plot."""

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Protocol, cast

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.markup import escape
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static

from cardak.core.config import ResourceSettings
from cardak.core.eventlog import line_text, log_lines
from cardak.core.history import (
    CELLS,
    DAYS,
    OTHER,
    SORTS,
    HistoryRow,
    Metric,
    band,
    edges,
    history_rows,
    legend,
    sort_rows,
)
from cardak.core.resources import GIB, Thresholds, derive
from cardak.core.snapshot import DeckSnapshot
from cardak.core.store import DayPeak, ResourceEvent, StoreError
from cardak.core.trends import (
    ALL,
    FIGURES,
    LABELS,
    DayUsage,
    Figure,
    GroupedTrends,
    UsageRow,
    usage_edges,
    usage_rows,
    value,
)
from cardak.ui.plot import BLANK, braille, level_row
from cardak.ui.widgets import fmt_count, fmt_usd

FIXED = (("project", 14), ("now", 9), ("median", 9), ("peak", 9), ("alerts", 9))
USAGE_FIXED = (("project", 14), ("today", 9), ("per day", 9), ("peak", 9), ("30 d", 9))
Mode = Metric | Figure
MODES: tuple[Mode, ...] = ("ram", "cpu", *FIGURES)
USAGE_SORTS = {"peak": "peak", "now": "today", "median": "per_day", "alerts": "window"}
BAND_STYLES = ("dim", "", "yellow", "dark_orange", "bold red")
EMPTY = "No history yet: the daemon records it (cardak resources install)."
NO_USAGE = "No usage yet: cardak counts it while the deck or the daemon runs."
PLOT_HEIGHT = 10
HOURS = DAYS * 24


class HistoryLog(Protocol):
    def history_days(self, since: float) -> list[DayPeak]: ...

    def history_medians(self, since: float) -> dict[str, tuple[float, float]]: ...

    def history_hours(self, subject: str, since: float) -> list[tuple[int, int, float]]: ...

    def alert_counts(self, since: float) -> dict[str, int]: ...

    def resource_events_since(self, ts: float) -> list[ResourceEvent]: ...


def fmt(value: float | None, metric: Metric) -> str:
    if value is None:
        return "-"
    return f"{value / GIB:.1f} GB" if metric == "ram" else f"{value:.0f}%"


def ufmt(v: float | None, figure: Figure, incomplete: bool = False) -> str:
    """A usage figure as the history screen shows it; "+" when some replies had no price."""
    if v is None:
        return "-"
    if figure == "cost":
        return fmt_usd(v) + ("+" if incomplete else "")
    if figure == "prompt_len":
        return f"{v:.0f} ch"
    return fmt_count(v)


def _usage_legend(e: Sequence[float], figure: Figure, unpriced: Sequence[str] = ()) -> str:
    parts = [f"{LABELS[figure]} per day, relative to the busiest day:"]
    if e[0] != float("inf"):
        parts.append(f"{CELLS[0]} <{ufmt(e[0], figure)}")
        parts += [f"{CELLS[i]} {ufmt(e[i - 1], figure)}–{ufmt(e[i], figure)}" for i in range(1, 4)]
        parts.append(f"{CELLS[-1]} {ufmt(e[-1], figure)}")
    if unpriced:
        parts.append(f"no price: {', '.join(unpriced)}")
    return "  ".join(parts)


def _usage_line(r: UsageRow, figure: Figure, e: Sequence[float]) -> list[Text]:
    """One usage row's cells: the fixed columns, then a shaded cell per day."""
    fixed = [
        r.name[:13].ljust(14),
        ufmt(r.today, figure, r.incomplete_today).rjust(9),
        ufmt(r.per_day, figure).rjust(9),
        ufmt(r.peak, figure).rjust(9),
        ufmt(r.window, figure, r.incomplete).rjust(9),
    ]
    days = [Text(CELLS[b], style=BAND_STYLES[b]) if (b := band(v, e)) >= 0 else Text(" ")
            for v in r.days]  # fmt: skip
    return [Text(x) for x in fixed] + days  # Text: names are free text, not markup


def sort_usage(rows: Sequence[UsageRow], key: str) -> list[UsageRow]:
    """As `sort_rows`: biggest first, Other and then All projects last."""
    if key == "name":
        ordered = sorted(rows, key=lambda r: r.name.lower())
    else:
        attr = USAGE_SORTS[key]
        ordered = sorted(rows, key=lambda r: (getattr(r, attr) is None, -(getattr(r, attr) or 0)))
    tail = (OTHER, ALL)
    return [r for r in ordered if r.subject not in tail] + [
        r for subject in tail for r in ordered if r.subject == subject
    ]


def _mode_name(mode: Mode) -> str:
    return mode.upper() if mode in ("ram", "cpu") else LABELS[cast(Figure, mode)]


def _day_note(name: str, day: date, d: DayUsage | None) -> str:
    if d is None:
        return f"{name} · {day:%a %b %-d}: no use"
    lines = [f"{LABELS[f]}: {ufmt(value(d, f), f, d.unpriced > 0)}" for f in FIGURES]
    return f"{name} · {day:%a %b %-d}\n" + "\n".join(lines)


def _thresholds(live: DeckSnapshot | None) -> Thresholds | None:
    if live is None:
        return None
    if live.thresholds is not None:
        return live.thresholds
    m = live.machine
    return derive(ResourceSettings(), m.mem_total, m.n_cpus) if m is not None else None


def _edges(t: Thresholds | None, metric: Metric) -> tuple[float, ...]:
    return edges(t, metric) if t is not None else (float("inf"),) * 4


def _first_day(today: date) -> date:
    return today - timedelta(days=DAYS - 1)


def _midnight(d: date) -> float:
    return datetime.combine(d, datetime.min.time()).timestamp()


class HistoryScreen(Screen[None]):
    BINDINGS = [
        Binding("c", "metric", "Next figure"),
        Binding("s", "sort", "Sort"),
        Binding("escape", "app.pop_screen", "Back"),
    ]
    CSS = """
    #title, #legend, #hint { padding: 0 1; }
    #axis { padding: 0; }  /* lines up with the table, which has no padding */
    #hint { color: $text-muted; }
    """

    def __init__(self, log: HistoryLog, live: DeckSnapshot | None, now: float) -> None:
        super().__init__()
        self._log = log
        self._live = live
        self._today = date.fromtimestamp(now)
        self._since = _midnight(_first_day(self._today))
        self._mode: Mode = "ram"
        self._sort = SORTS[0]
        self._rows: list[HistoryRow] = []
        self._usage: list[UsageRow] = []
        self._error: str | None = None
        try:
            self._days = log.history_days(self._since)
            self._medians = log.history_medians(self._since)
            self._counts = log.alert_counts(self._since)
        except StoreError as e:
            self._days, self._medians, self._counts = [], {}, {}
            self._error = f"History unavailable: {e}"

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("", id="title")
        yield Static("", id="axis")
        yield DataTable(cursor_type="cell", cell_padding=0, id="history")
        yield Static("", id="legend")
        yield Static("", id="hint")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#axis", Static).update(self._axis())
        self._fill()
        self.query_one(DataTable).focus()

    @property
    def _trends(self) -> GroupedTrends | None:
        return self._live.usage_trends if self._live is not None else None

    def _columns(self, fixed: Sequence[tuple[str, int]]) -> None:
        table = self.query_one(DataTable)
        table.clear(columns=True)
        for name, width in fixed:
            table.add_column(name.rjust(width) if name != "project" else name.ljust(width))
        for _ in range(DAYS):
            table.add_column(" ", width=1)

    def _title(self) -> None:
        mode = self._mode
        following = MODES[(MODES.index(mode) + 1) % len(MODES)]
        name, nxt = _mode_name(mode), _mode_name(following)
        keys = f"[c] next: {nxt}  [s] sort: {self._sort}"
        title = f"history · last {DAYS} days · {name}      {keys}"
        self.query_one("#title", Static).update(Text(title))  # Text: "[c]" is not markup

    def _fill(self) -> None:
        if self._mode in ("ram", "cpu"):
            self._fill_resources(cast(Metric, self._mode))
        else:
            self._fill_usage(cast(Figure, self._mode))
        self._title()

    def _axis(self) -> str:
        first = _first_day(self._today)
        offset = sum(width for _, width in FIXED)
        line = [" "] * (offset + DAYS + 8)
        for index in (0, 14, DAYS - 1):
            label = f"{first + timedelta(days=index):%b %-d}"
            start = offset + index - (len(label) - 1 if index == DAYS - 1 else 0)
            line[start : start + len(label)] = label
        return "".join(line).rstrip()

    def _fill_resources(self, metric: Metric) -> None:
        self._columns(FIXED)
        hint = self._error or (EMPTY if not self._days else "")
        self.query_one("#hint", Static).update(Text(hint))
        rows = history_rows(
            self._days, self._medians, self._counts, self._live, self._today, metric
        )
        self._rows = sort_rows(rows, self._sort)
        e = _edges(_thresholds(self._live), metric)
        table = self.query_one(DataTable)
        for r in self._rows:
            alerts = "-" if r.alerts is None else str(r.alerts)
            fixed = [  # Text: DataTable parses plain strings as markup, and names are free text
                r.name[:13].ljust(14),
                fmt(r.now, metric).rjust(9),
                fmt(r.median, metric).rjust(9),
                fmt(r.peak, metric).rjust(9),
                alerts.rjust(7),
            ]
            cells = [
                Text(CELLS[b], style=BAND_STYLES[b]) if (b := band(v, e)) >= 0 else Text(" ")
                for v in r.days
            ]
            table.add_row(*(Text(x) for x in fixed), *cells, key=r.subject)
        self.query_one("#legend", Static).update(legend(e, metric) if e[0] != float("inf") else "")

    def _fill_usage(self, figure: Figure) -> None:
        self._columns(USAGE_FIXED)
        grouped = self._trends
        self._usage = sort_usage(usage_rows(grouped, figure) if grouped else [], self._sort)
        self.query_one("#hint", Static).update(Text("" if self._usage else NO_USAGE))
        e = usage_edges([r for r in self._usage if r.subject != ALL])
        e_all = usage_edges([r for r in self._usage if r.subject == ALL])
        table = self.query_one(DataTable)
        for r in self._usage:
            table.add_row(*_usage_line(r, figure, e_all if r.subject == ALL else e), key=r.subject)
        unpriced = grouped.unpriced if grouped is not None and figure == "cost" else ()
        text = _usage_legend(e, figure, unpriced) if self._usage else ""
        self.query_one("#legend", Static).update(Text(text))

    def action_metric(self) -> None:
        self._mode = MODES[(MODES.index(self._mode) + 1) % len(MODES)]
        self._fill()

    def action_sort(self) -> None:
        self._sort = SORTS[(SORTS.index(self._sort) + 1) % len(SORTS)]
        self._fill()

    def _note_day(self, row: int, index: int) -> None:
        grouped = self._trends
        if grouped is None or row >= len(self._usage):
            return
        subject = self._usage[row].subject
        trend = next(t for t in grouped.rows if t.subject == subject)
        day = _first_day(grouped.today) + timedelta(days=index)
        self.notify(escape(_day_note(trend.name, day, trend.days[index])))

    @on(DataTable.CellSelected)
    def _open_day(self, event: DataTable.CellSelected) -> None:
        index = event.coordinate.column - len(FIXED)
        if index < 0:
            return
        if self._mode not in ("ram", "cpu"):
            return self._note_day(event.coordinate.row, index)
        if event.coordinate.row >= len(self._rows):
            return
        metric = cast(Metric, self._mode)
        row = self._rows[event.coordinate.row]
        try:
            hours = self._log.history_hours(row.subject, self._since)
            events = [
                e for e in self._log.resource_events_since(self._since) if e.subject == row.subject
            ]
        except StoreError as e:
            self.notify(escape(f"History unavailable: {e}"), severity="error")
            return
        t = _thresholds(self._live)
        threshold = None
        if t is not None and row.subject != OTHER:  # Other never alerts: no line to cross
            threshold = float(t.pane_ram if metric == "ram" else t.pane_cpu_pct)
        self.app.push_screen(
            PlotScreen(
                row.name,
                row.subject,
                metric,
                hours,
                threshold,
                _first_day(self._today) + timedelta(days=index),
                events,
                self._today,
            )  # fmt: skip
        )


class PlotScreen(Screen[None]):
    BINDINGS = [
        Binding("left", "move(-1)", "Day before"),
        Binding("right", "move(1)", "Day after"),
        Binding("escape", "app.pop_screen", "Back"),
    ]
    CSS = "#plot, #plot-title, #day { padding: 0 1; }"

    def __init__(
        self,
        name: str,
        subject: str,
        metric: Metric,
        hours: Sequence[tuple[int, int, float]],
        threshold: float | None,
        day: date,
        events: Sequence[ResourceEvent],
        today: date,
    ) -> None:
        super().__init__()
        self._name = name
        self._subject = subject
        self._metric: Metric = metric
        self._threshold = threshold
        self._day = day
        self._events = events
        self._today = today
        self._first = _first_day(today)
        start = _midnight(self._first)
        self._values: list[float | None] = [None] * HOURS
        for hour, ram, cpu in hours:
            index = int((hour - start) // 3600)
            if 0 <= index < HOURS:
                self._values[index] = float(ram if metric == "ram" else cpu)

    def compose(self) -> ComposeResult:
        yield Header()
        title = Text(f"{self._name} · {self._metric.upper()} · last {DAYS} days")
        yield Static(title, id="plot-title")
        yield Static("", id="plot")
        yield Static("", id="day")
        yield Footer()

    def on_mount(self) -> None:
        self._draw()

    def on_resize(self) -> None:
        self._draw()

    def _width(self) -> int:
        return max(DAYS, min(self.size.width - 8, 120))

    def _draw(self) -> None:
        width = self._width()
        seen = [v for v in self._values if v is not None]
        top = max([*seen, self._threshold or 0.0, 1.0])
        lines = braille(self._values, top, width, PLOT_HEIGHT)
        index = (self._day - self._first).days
        lo, hi = index * width // DAYS, max(index * width // DAYS + 1, (index + 1) * width // DAYS)
        line_at = level_row(self._threshold, top, PLOT_HEIGHT) if self._threshold else None
        plot = Text()
        for row, line in enumerate(lines):
            for x, char in enumerate(line):
                style = "reverse" if lo <= x < hi else ""
                if row == line_at and char == BLANK:
                    plot.append("─", style=f"red {style}".strip())
                else:
                    plot.append(char, style=style)
            plot.append("\n")
        axis = [" "] * width
        for d in (0, 14, DAYS - 1):
            label = f"{self._first + timedelta(days=d):%b %-d}"
            start = min(d * width // DAYS, width - len(label))
            axis[start : start + len(label)] = label
        plot.append("".join(axis))
        self.query_one("#plot", Static).update(plot)
        self.query_one("#day", Static).update(Text(self._day_text()))  # alert texts: not markup

    def _day_text(self) -> str:
        start = _midnight(self._day)
        end = start + 86_400
        values = [
            v for i, v in enumerate(self._values)
            if v is not None and start <= _midnight(self._first) + i * 3600 < end
        ]  # fmt: skip
        events = [e for e in self._events if start <= e.ts < end]
        notified = sum(e.notified for e in events if e.kind not in ("crash", "daemon"))
        when = f"{self._day:%b %-d}"
        if not values:
            head = f"{when}: no data"
        else:
            unit = "alert" if notified == 1 else "alerts"
            head = f"{when}: peak {fmt(max(values), self._metric)} · {notified} {unit}"
        return "\n".join([head, *(line_text(x) for x in log_lines(events))])

    def action_move(self, step: int) -> None:
        day = self._day + timedelta(days=step)
        if self._first <= day <= self._today:
            self._day = day
            self._draw()
