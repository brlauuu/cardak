"""30 days per project: the history table (`h`) and one project's day plot."""

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Protocol

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
from cardak.ui.plot import BLANK, braille, level_row

FIXED = (("project", 14), ("now", 9), ("median", 9), ("peak", 9), ("alerts", 7))
BAND_STYLES = ("dim", "", "yellow", "dark_orange", "bold red")
EMPTY = "No history yet: the daemon records it (cardak resources install)."
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
        Binding("c", "metric", "RAM/CPU"),
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
        self._metric: Metric = "ram"
        self._sort = SORTS[0]
        self._rows: list[HistoryRow] = []
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
        table = self.query_one(DataTable)
        for name, width in FIXED:
            table.add_column(name.rjust(width) if name != "project" else name.ljust(width))
        for _ in range(DAYS):
            table.add_column(" ", width=1)
        self.query_one("#axis", Static).update(self._axis())
        hint = self._error or (EMPTY if not self._days else "")
        self.query_one("#hint", Static).update(Text(hint))
        self._fill()
        table.focus()

    def _axis(self) -> str:
        first = _first_day(self._today)
        offset = sum(width for _, width in FIXED)
        line = [" "] * (offset + DAYS + 8)
        for index in (0, 14, DAYS - 1):
            label = f"{first + timedelta(days=index):%b %-d}"
            start = offset + index - (len(label) - 1 if index == DAYS - 1 else 0)
            line[start : start + len(label)] = label
        return "".join(line).rstrip()

    def _fill(self) -> None:
        metric = self._metric
        rows = history_rows(
            self._days, self._medians, self._counts, self._live, self._today, metric
        )
        self._rows = sort_rows(rows, self._sort)
        e = _edges(_thresholds(self._live), metric)
        table = self.query_one(DataTable)
        table.clear()
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
        other = "CPU" if metric == "ram" else "RAM"
        keys = f"[c] {other}  [s] sort: {self._sort}"
        title = f"history · last {DAYS} days · {metric.upper()}      {keys}"
        self.query_one("#title", Static).update(Text(title))  # Text: "[c]" is not markup
        self.query_one("#legend", Static).update(legend(e, metric) if e[0] != float("inf") else "")

    def action_metric(self) -> None:
        self._metric = "cpu" if self._metric == "ram" else "ram"
        self._fill()

    def action_sort(self) -> None:
        self._sort = SORTS[(SORTS.index(self._sort) + 1) % len(SORTS)]
        self._fill()

    @on(DataTable.CellSelected)
    def _open_day(self, event: DataTable.CellSelected) -> None:
        index = event.coordinate.column - len(FIXED)
        if index < 0 or event.coordinate.row >= len(self._rows):
            return
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
            threshold = float(t.pane_ram if self._metric == "ram" else t.pane_cpu_pct)
        self.app.push_screen(
            PlotScreen(
                row.name,
                row.subject,
                self._metric,
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
