"""The machine's section of the wide deck's side pane (#185): CPU and RAM, each a figure and a
plot of the last minutes, swap, and the projects using the most RAM.

It gets at most half the pane's height. With less than it needs it drops, in this order, the
projects (smallest first), swap, the plots' rows and then the plots; a full swap is never dropped.
"""

from collections.abc import Sequence

from rich.text import Text
from textual import events
from textual.containers import Vertical
from textual.widgets import Static

from cardak.core.snapshot import Group, MachineView
from cardak.ui.plot import braille
from cardak.ui.widgets import OVER, fit_line, fmt_cpu, fmt_rss, share

TITLE = "Machine"
PLOT_ROWS = 3
TOP_PROJECTS = 3
GIB = 2**30


def _cpu_line(view: MachineView, width: int) -> Text:
    m = view.sample
    cores = Text(f"{m.n_cpus} core{'' if m.n_cpus == 1 else 's'}", style="dim")
    return fit_line((Text(f"CPU {fmt_cpu(m.cpu_pct)}"), cores), width)


def _ram_line(view: MachineView) -> Text:
    m = view.sample
    text = Text(
        f"RAM {m.mem_used / GIB:.1f} / {m.mem_total / GIB:.1f}G{share(m.mem_used, m.mem_total)}"
    )
    if view.level is not None:
        free = round(m.mem_available / m.mem_total * 100)
        text.append(f"  ⚠ {free}% free", style=OVER if view.level == "critical" else "yellow")
    return text


def _swap_line(view: MachineView) -> Text | None:
    """None on a machine with no swap."""
    m = view.sample
    if m.swap_total <= 0:
        return None
    used = m.swap_total - m.swap_free
    text = Text(f"swap {used / GIB:.1f} / {m.swap_total / GIB:.1f}G")
    if m.swap_free == 0:
        text.append("  full")
        text.stylize(OVER)
    return text


def _plot(values: Sequence[float | None], top: float, width: int, rows: int) -> list[Text]:
    if rows < 1:
        return []
    return [Text(line, style="cyan") for line in braille(values, top, width, rows)]


def _project_lines(groups: Sequence[Group], total: int, width: int) -> list[Text]:
    """The TOP_PROJECTS projects using the most RAM, biggest first, then Other."""
    projects = sorted(
        (g for g in groups if g.kind in ("config", "git") and g.rss > 0),
        key=lambda g: g.rss,
        reverse=True,
    )[:TOP_PROJECTS]
    shown = projects + [g for g in groups if g.kind == "other" and g.rss > 0]
    return [
        fit_line(
            (
                Text(g.name),
                Text(f" {fmt_rss(g.rss)}{share(g.rss, total)}", style=OVER if g.over else ""),
            ),
            width,
        )
        for g in shown
    ]


def machine_lines(
    view: MachineView, groups: Sequence[Group], width: int, budget: int | None = None
) -> list[Text]:
    """The section's lines, none wider than `width`, at most `budget` of them (None: all of
    it) but never fewer than its title and two figures, and a full swap."""
    m = view.sample
    swap = _swap_line(view)
    full = swap is not None and m.swap_free == 0
    projects = _project_lines(groups, m.mem_total, width)
    rows = PLOT_ROWS
    if budget is not None:
        spare = budget - (3 + 2 * PLOT_ROWS + (1 if full else 0))  # after figures and plots
        if swap is not None and not full:
            swap = swap if spare >= 1 else None
            spare -= 1 if swap is not None else 0
        projects = projects[: max(spare, 0)]
        if spare < 0:
            rows = max(PLOT_ROWS + spare // 2, 0)  # both plots lose a row together
    lines = [Text(TITLE, style="bold")]
    lines.append(_cpu_line(view, width))
    lines += _plot(view.cpu_series, 100.0, width, rows)
    ram = _ram_line(view)
    ram.truncate(width, overflow="ellipsis")
    lines.append(ram)
    lines += _plot(view.ram_series, m.mem_total, width, rows)
    if swap is not None:
        lines.append(swap)
    return lines + projects


class MachinePane(Static):
    DEFAULT_CSS = """
    MachinePane { height: auto; border-top: solid $panel-lighten-1; }
    """

    def __init__(self, width: int, *, id: str | None = None) -> None:  # noqa: A002
        super().__init__("", id=id)
        self._width = width
        self._budget: int | None = None
        self._view: MachineView | None = None
        self._groups: tuple[Group, ...] = ()

    def show(self, view: MachineView | None, groups: Sequence[Group]) -> None:
        self._view, self._groups = view, tuple(groups)
        self._draw()

    def set_room(self, height: int) -> None:
        """`height` lines are the section's to use, its top border among them."""
        self._budget = max(height - 1, 0)
        self._draw()

    def _draw(self) -> None:
        if self._view is None:
            self.update("")
            return
        lines = machine_lines(self._view, self._groups, self._width, self._budget)
        self.update(Text("\n").join(lines))


class SidePane(Vertical):
    """The side pane: it gives the machine's section half its height, whatever that becomes."""

    def on_resize(self, event: events.Resize) -> None:
        for pane in self.query(MachinePane):
            pane.set_room(event.size.height // 2)
