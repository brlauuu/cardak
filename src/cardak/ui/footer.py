"""The footer every screen shows: Textual's, wrapped onto more rows when its keys don't fit (#179).

Textual's `Footer` is one row that cuts off whatever is past the screen's edge. This one
lays the same keys out row by row, filling each before starting the next, and lays them
out again when the screen's width changes which row a key falls on.
"""

from __future__ import annotations

from rich.cells import cell_len
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widget import Widget
from textual.widgets import Footer
from textual.widgets._footer import FooterKey

# FooterKey's padding (Textual 8.2.8; FooterKey is private, so check after upgrading):
# one cell either side of the key, one after the description.
KEY_PADDING = 2
DESCRIPTION_PADDING = 1


def key_width(key: Widget) -> int:
    """The cells a footer key takes up, from what it shows (it isn't laid out yet)."""
    if not isinstance(key, FooterKey):
        return 0
    width = cell_len(key.key_display) + KEY_PADDING
    if key.description:
        width += cell_len(key.description) + DESCRIPTION_PADDING
    return width


def _greedy(widths: list[int], limit: int) -> list[int]:
    sizes: list[int] = []
    used = count = 0
    for w in widths:
        if count and used + w > limit:
            sizes.append(count)
            used = count = 0
        used += w
        count += 1
    sizes.append(count)
    return sizes


def row_sizes(widths: list[int], width: int) -> tuple[int, ...]:
    """How many keys go on each row: as few rows as fit the width, evened out.

    The rows are as many as filling each one in turn would need, and each is kept as short
    as that number of rows allows, so a second row isn't one key long. A width of 0 (before
    the footer's first layout) puts everything on one row, and a key wider than the screen
    gets a row of its own.
    """
    if not widths:
        return ()
    if width <= 0:
        return (len(widths),)
    rows = len(_greedy(widths, width))
    low, high = max(widths), max(width, max(widths))
    while low < high:  # the narrowest limit that still needs no more rows
        mid = (low + high) // 2
        if len(_greedy(widths, mid)) <= rows:
            high = mid
        else:
            low = mid + 1
    return tuple(_greedy(widths, low))


class WrappingFooter(Footer):
    """`Footer`, with its keys on as many rows as they need."""

    DEFAULT_CSS = """
    WrappingFooter {
        layout: vertical;
        height: auto;
    }
    WrappingFooter > .footer-row {
        layout: horizontal;
        width: 1fr;
        height: 1;
    }
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._widths: list[int] = []
        self._sizes: tuple[int, ...] = ()

    def compose(self) -> ComposeResult:
        # No cardak binding has a group, so Footer yields its keys one by one (a group's
        # keys would go inside its KeyGroup, which this doesn't lay out).
        keys = list(super().compose())
        self._widths = [key_width(key) for key in keys]
        self._sizes = row_sizes(self._widths, self.size.width)
        start = 0
        for size in self._sizes:
            yield Horizontal(*keys[start : start + size], classes="footer-row")
            start += size

    def on_resize(self, event: events.Resize) -> None:
        if row_sizes(self._widths, event.size.width) != self._sizes:
            self.call_after_refresh(self.recompose)
