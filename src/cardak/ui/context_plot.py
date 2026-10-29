"""One project's sessions over one day (#86): each session's context as a line, on a log scale."""

from collections.abc import Sequence
from datetime import date, datetime, timedelta

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Header, Static

from cardak.core.context import (
    TICKS,
    ProjectContext,
    SessionLine,
    day_points,
    drop_indices,
    position,
)
from cardak.core.trends import DAYS, first_day
from cardak.ui.plot import BLANK, braille_cells, level_row
from cardak.ui.widgets import fmt_count

PALETTE = ("cyan", "magenta", "yellow", "green", "blue", "red", "bright_cyan", "bright_magenta")
PLOT_HEIGHT = 12
TICK_LABELS = {10_000: "10k", 100_000: "100k", 1_000_000: "1M"}
LABEL = 5  # "100k " left of the plot
DAY_S = 86_400


def _midnight(d: date) -> float:
    return datetime.combine(d, datetime.min.time()).timestamp()


def _columns(points: list[tuple[float, int]], start: float, n: int) -> list[float | None]:
    """One session's dot columns over the day: each column the highest point in it, and the
    columns between its first and last point held at the last value, so the line is unbroken
    and a drop is a vertical stroke."""
    cols: list[float | None] = [None] * n
    for ts, ctx in points:
        c = min(n - 1, max(0, int((ts - start) / DAY_S * n)))
        cols[c] = max(cols[c] or 0.0, position(ctx))
    held: float | None = None
    last = max((i for i, v in enumerate(cols) if v is not None), default=-1)
    for i in range(last + 1):
        if cols[i] is None:
            cols[i] = held
        held = cols[i]
    return cols


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def session_text(line: SessionLine, day: date) -> str:
    """ "01:00–03:00  peak 300k · 1 drop · last 150k": one session's part of the day."""
    points = day_points(line, day)
    start, end = _midnight(day), _midnight(day + timedelta(days=1))
    drops = sum(start <= line.points[i][0] < end for i in drop_indices(line.points))
    first, last = (datetime.fromtimestamp(points[i][0]) for i in (0, -1))
    peak = max(ctx for _, ctx in points)
    return (
        f"{first:%H:%M}–{last:%H:%M}  peak {fmt_count(peak)} · {_plural(drops, 'drop')}"
        f" · last {fmt_count(points[-1][1])}"
    )


def _merged(
    lines: Sequence[SessionLine], day: date, width: int
) -> tuple[list[list[int]], list[list[int]]]:
    """Every session's dots in one grid, and which session set each character last."""
    start = _midnight(day)
    bits = [[0] * width for _ in range(PLOT_HEIGHT)]
    owner = [[-1] * width for _ in range(PLOT_HEIGHT)]
    for i, ln in enumerate(lines):
        cols = _columns(day_points(ln, day), start, width * 2)
        for y, cells in enumerate(braille_cells(cols, 1.0, width, PLOT_HEIGHT)):
            for x, b in enumerate(cells):
                if b:
                    bits[y][x] |= b
                    owner[y][x] = i  # a shared character takes the later session's color
    return bits, owner


def _hours(width: int) -> str:
    axis = [" "] * (LABEL + width)
    for hour in (0, 6, 12, 18):
        label = f"{hour:02d}:00"
        at = LABEL + hour * width // 24
        axis[at : at + len(label)] = label
    return "".join(axis).rstrip()


def plot_text(lines: Sequence[SessionLine], day: date, width: int) -> Text:
    """The sessions' lines over the day, with the log scale's gridlines. Colors go by order
    among the sessions with a point that day, as the list under the plot numbers them."""
    bits, owner = _merged([ln for ln in lines if day_points(ln, day)], day, width)
    grid = {level_row(position(t), 1.0, PLOT_HEIGHT): TICK_LABELS[t] for t in TICKS}
    plot = Text()
    for y in range(PLOT_HEIGHT):
        plot.append(f"{grid.get(y, ''):>4} ", style="dim")
        for x in range(width):
            if bits[y][x]:
                plot.append(chr(0x2800 + bits[y][x]), style=PALETTE[owner[y][x] % len(PALETTE)])
            else:
                plot.append("─" if y in grid else BLANK, style="dim" if y in grid else "")
        plot.append("\n")
    return plot.append(_hours(width), style="dim")


class ContextPlotScreen(Screen[None]):
    BINDINGS = [
        Binding("left", "move(-1)", "Day before"),
        Binding("right", "move(1)", "Day after"),
        Binding("escape", "app.pop_screen", "Back"),
    ]
    CSS = "#plot, #plot-title, #day { padding: 0 1; }"

    def __init__(self, row: ProjectContext, day: date, today: date) -> None:
        super().__init__()
        self._row = row
        self._day = day
        self._today = today
        self._first = first_day(today)

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("", id="plot-title")
        yield Static("", id="plot")
        yield Static("", id="day")
        yield Footer()

    def on_mount(self) -> None:
        self._draw()

    def on_resize(self) -> None:
        self._draw()

    def _width(self) -> int:
        return max(48, min(self.size.width - 8 - LABEL, 120))

    def _lines(self) -> list[SessionLine]:
        return [ln for ln in self._row.lines if day_points(ln, self._day)]

    def _draw(self) -> None:
        lines = self._lines()
        title = f"{self._row.name} · context · {self._day:%a %b %-d}"
        self.query_one("#plot-title", Static).update(Text(title))  # names are not markup
        self.query_one("#plot", Static).update(plot_text(lines, self._day, self._width()))
        self.query_one("#day", Static).update(self._day_text(lines))

    def _day_text(self, lines: list[SessionLine]) -> Text:
        if not lines:
            return Text(f"{self._day:%b %-d}: no sessions")
        text = Text()
        for i, ln in enumerate(lines):
            if i:
                text.append("\n")
            text.append("● ", style=PALETTE[i % len(PALETTE)]).append(session_text(ln, self._day))
        return text

    def action_move(self, step: int) -> None:
        day = self._day + timedelta(days=step)
        if self._first <= day <= self._today and (day - self._first).days < DAYS:
            self._day = day
            self._draw()
