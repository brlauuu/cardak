"""The PM view (`P` every project, `p` one, #68): a band per project with todo, onit and done.

Each column scrolls on its own. ↑/↓ move within a column and, past its first or last line, to
the same column of the neighbouring band; ←/→ move between columns. Enter opens the card on
GitHub; `R` reads GitHub again for what's shown.
"""

import re
import time
import webbrowser
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import Screen
from textual.widgets import Footer, Header, OptionList, Static
from textual.widgets.option_list import Option

from cardak.collectors.github import Label
from cardak.core.agents import Status
from cardak.core.pm import Band, Board, Card, Done
from cardak.core.pm_service import Observed
from cardak.core.recap import ago
from cardak.ui.widgets import IDLE, WAITING, WORKING

LOADING = "Reading GitHub…"
_HEX = re.compile(r"^[0-9a-fA-F]{6}$")
_STATE_STYLE: dict[Status, str] = {"waiting": f"bold {WAITING}", "working": WORKING, "idle": IDLE}


class PmView(Protocol):
    def board(self) -> Board | None: ...

    def refresh(self, project: Path | None = None) -> None: ...

    def observe(self, sessions: Sequence[Observed]) -> None: ...


def chip(label: Label) -> Text:
    """A label in GitHub's color, with black or white text by its brightness."""
    if not _HEX.match(label.color):
        return Text(f" {label.name} ", style="reverse")
    r, g, b = (int(label.color[i : i + 2], 16) for i in (0, 2, 4))
    ink = "black" if 0.299 * r + 0.587 * g + 0.114 * b > 150 else "white"
    return Text(f" {label.name} ", style=f"{ink} on #{label.color.lower()}")


def card_text(card: Card, now: float) -> Text:
    text = Text(f"#{card.number} {card.title}")
    for label in card.labels:
        text.append(" ")
        text.append_text(chip(label))
    parts: list[str] = []
    if card.branch:
        parts.append(card.branch)
    if card.pr is not None:
        parts.append(f"PR #{card.pr} open")
    if card.kind == "pr":
        parts.reverse()
    text.append("\n")
    text.append(" · ".join(parts) if parts else f"updated {ago(now - card.updated_at)}", "dim")
    if card.session is not None:
        text.append("  ")
        text.append(f"● {card.session}", _STATE_STYLE[card.session])
    return text


def band_title(band: Band, now: float) -> Text:
    issues = len(band.todo) + sum(c.kind == "issue" for c in band.onit)
    text = Text(band.name, style="bold")
    if band.repo is not None and (band.fetched_at is not None or not band.error):
        text.append(f" · {issues} open", "dim")
    if band.fetched_at is not None:
        text.append(f" · fetched {ago(now - band.fetched_at)}", "dim")
    if band.error:
        text.append(f" · {band.error}", "yellow")
    return text


def _done_options(done: Done) -> tuple[list[Option], list[str | None]]:
    options: list[Option] = []
    urls: list[str | None] = []
    if done.note:
        options.append(Option(Text(done.note, style="dim italic"), disabled=True))
        urls.append(None)
    for b in done.bullets:
        options.append(Option(Text(f"{b.text} " + " ".join(f"#{r}" for r in b.refs))))
        urls.append(b.url)
    return options, urls


class Column(OptionList):
    """One column of one band; it asks the screen to move focus at its edges."""

    BINDINGS = [
        Binding("left", "side(-1)", show=False),
        Binding("right", "side(1)", show=False),
    ]

    class Move(Message):
        def __init__(self, column: "Column", bands: int, columns: int) -> None:
            super().__init__()
            self.column, self.bands, self.columns = column, bands, columns

    def __init__(self, band: int, index: int) -> None:
        super().__init__(classes="col")
        self.band, self.index = band, index
        self.urls: list[str | None] = []

    def show(self, options: Sequence[Option], urls: Sequence[str | None]) -> None:
        """Replace the lines, keeping the highlighted one by its URL."""
        was = self.highlighted
        keep = self.urls[was] if was is not None and was < len(self.urls) else None
        self.clear_options()
        self.add_options(list(options))
        self.urls = list(urls)
        if keep is not None and keep in self.urls:
            self.highlighted = self.urls.index(keep)
        elif was is not None and self.option_count:
            self.highlighted = min(was, self.option_count - 1)

    def _enabled(self) -> list[int]:
        return [i for i in range(self.option_count) if not self.get_option_at_index(i).disabled]

    def action_cursor_up(self) -> None:
        enabled = self._enabled()
        if not enabled or self.highlighted is None or self.highlighted <= enabled[0]:
            self.post_message(self.Move(self, -1, 0))
        else:
            super().action_cursor_up()

    def action_cursor_down(self) -> None:
        enabled = self._enabled()
        if not enabled or self.highlighted is None or self.highlighted >= enabled[-1]:
            self.post_message(self.Move(self, 1, 0))
        else:
            super().action_cursor_down()

    def action_side(self, step: int) -> None:
        self.post_message(self.Move(self, 0, step))


class PmScreen(Screen[None]):
    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("R", "refresh", "Refresh"),
    ]
    CSS = """
    #loading { padding: 1 2; }
    .title { padding: 0 1; margin-top: 1; }
    .cols { height: 14; }
    PmScreen.one .cols { height: 1fr; }
    PmScreen.one #bands { height: 1fr; }
    .colbox { width: 1fr; }
    .head { padding: 0 1; color: $text-muted; }
    .col { height: 1fr; border: none; }
    """

    def __init__(
        self,
        view: PmView,
        project: Path | None,
        open_url: Callable[[str], object] = webbrowser.open,
        clock: Callable[[], float] = time.time,
    ) -> None:
        super().__init__(classes="one" if project is not None else "")
        self._view = view
        self._project = project
        self._open_url = open_url
        self._clock = clock
        self._shown: Board | None = None
        self._names: tuple[str, ...] | None = None
        self._grid: list[list[Column]] = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(LOADING, id="loading")
        yield VerticalScroll(id="bands")
        yield Footer()

    async def on_mount(self) -> None:
        self.title = "PM · all projects" if self._project is None else "PM"
        await self._tick()
        self.set_interval(1.0, self._tick)

    def _bands(self, board: Board) -> list[Band]:
        if self._project is None:
            return list(board.bands)
        return [b for b in board.bands if Path(b.path) == self._project]

    async def _tick(self) -> None:
        board = self._view.board()
        if board is None or board is self._shown:
            return
        self._shown = board
        bands = self._bands(board)
        self.query_one("#loading", Static).display = not bands
        names = tuple(b.name for b in bands)
        if names != self._names:
            self._names = names
            await self._build(bands)
        self._fill(bands)

    async def _build(self, bands: Sequence[Band]) -> None:
        holder = self.query_one("#bands", VerticalScroll)
        await holder.remove_children()
        self._grid = []
        for i, _band in enumerate(bands):
            columns = [Column(i, c) for c in range(3)]
            self._grid.append(columns)
            boxes = [Vertical(Static("", classes="head"), col, classes="colbox") for col in columns]
            await holder.mount(Static("", classes="title"), Horizontal(*boxes, classes="cols"))
        if self._grid:
            self.call_after_refresh(self._focus, self._grid[0][0], 0)

    def _fill(self, bands: Sequence[Band]) -> None:
        now = self._clock()
        titles = list(self.query(".title").results(Static))
        for band, title, columns in zip(bands, titles, self._grid, strict=False):
            title.update(band_title(band, now))
            todo, onit, done = columns
            self._head(todo).update(f"todo ({len(band.todo)})")
            self._head(onit).update(f"onit ({len(band.onit)})")
            self._head(done).update("done · 7 days")
            todo.show([Option(card_text(c, now)) for c in band.todo], [c.url for c in band.todo])
            onit.show([Option(card_text(c, now)) for c in band.onit], [c.url for c in band.onit])
            done.show(*_done_options(band.done))

    def _head(self, column: Column) -> Static:
        parent = column.parent
        assert parent is not None
        return parent.query_one(".head", Static)

    def _focus(self, column: Column, at: int) -> None:
        column.focus()
        enabled = column._enabled()  # noqa: SLF001
        if enabled:
            column.highlighted = enabled[0] if at == 0 else enabled[-1]
        column.scroll_visible()

    @on(Column.Move)
    def _move(self, event: Column.Move) -> None:
        b, c = event.column.band, event.column.index
        if event.bands:
            b += event.bands
            if 0 <= b < len(self._grid):
                self._focus(self._grid[b][c], 0 if event.bands > 0 else -1)
            return
        c += event.columns
        if c > 2 and b + 1 < len(self._grid):
            b, c = b + 1, 0
        elif c < 0 and b > 0:
            b, c = b - 1, 2
        if 0 <= c <= 2:
            target = self._grid[b][c]
            self._focus(target, 0) if target.highlighted is None else target.focus()

    @on(OptionList.OptionSelected)
    def _open(self, event: OptionList.OptionSelected) -> None:
        column = event.option_list
        if isinstance(column, Column) and event.option_index < len(column.urls):
            url = column.urls[event.option_index]
            if url:
                self._open_url(url)

    def action_refresh(self) -> None:
        self._view.refresh(self._project)
        self.notify("Reading GitHub…")
