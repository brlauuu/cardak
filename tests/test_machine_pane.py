"""The machine's section of the side pane (#185)."""

from dataclasses import replace
from pathlib import Path

import pytest
from rich.text import Text

from cardak.collectors.machine import MachineSample
from cardak.core.snapshot import Group, MachineView
from cardak.ui.machine_pane import PLOT_ROWS, machine_lines
from cardak.ui.widgets import OVER

GIB = 2**30
WIDTH = 39
BRAILLE = range(0x2800, 0x2900)


def view(
    level: str | None = None,
    *,
    available: float = 10,
    cpu: float | None = 38.0,
    swap_total: float = 8,
    swap_free: float = 6.8,
    n_cpus: int = 16,
) -> MachineView:
    m = MachineSample(
        32 * GIB, int(available * GIB), int(swap_total * GIB), int(swap_free * GIB), cpu, n_cpus
    )
    ram = tuple(float(i) * GIB for i in range(1, 31))
    return MachineView(m, level, "▄", "▂", ram, (None, 10.0, 90.0, 40.0))  # type: ignore[arg-type]


def group(name: str, gib: float, kind: str = "config", over: bool = False) -> Group:
    return Group(kind, Path("/repos") / name, name, None, None, 1.0, int(gib * GIB), (), over=over)  # type: ignore[arg-type]


GROUPS = (
    group("webshop", 4),
    group("tiny", 0.5),
    group("api", 6, kind="git"),
    group("docs", 2),
    group("Other", 3, kind="other"),
    group("Waiting on you", 9, kind="waiting"),
)


def plain(lines: list[Text]) -> list[str]:
    return [line.plain.rstrip() for line in lines]


def is_plot(line: Text) -> bool:
    return bool(line.plain) and all(ord(c) in BRAILLE for c in line.plain)


def test_the_whole_section() -> None:
    lines = machine_lines(view(), GROUPS, WIDTH)
    text = plain(lines)
    assert text[0] == "Machine"
    assert text[1] == "CPU 38%" + " " * 24 + "16 cores"
    assert all(is_plot(line) for line in lines[2:5] + lines[6:9])
    assert text[5] == "RAM 22.0 / 32.0G (69%)"
    assert text[9] == "swap 1.2 / 8.0G"
    # the three biggest projects, biggest first, then Other; never the waiting group
    assert text[10:] == [
        "api" + " " * 26 + "6.0G (19%)",
        "webshop" + " " * 22 + "4.0G (13%)",
        "docs" + " " * 26 + "2.0G (6%)",
        "Other" + " " * 25 + "3.0G (9%)",
    ]
    assert all(line.cell_len <= WIDTH for line in lines)


def test_plots_draw_the_series() -> None:
    lines = machine_lines(view(), (), WIDTH)
    assert any(c != "⠀" for line in lines[2:5] for c in line.plain)  # CPU, past its gap
    assert any(c != "⠀" for line in lines[6:9] for c in line.plain)
    empty = machine_lines(replace(view(), ram_series=(), cpu_series=()), (), WIDTH)
    assert all(set(line.plain) == {"⠀"} for line in empty[2:5] + empty[6:9])


@pytest.mark.parametrize(
    ("budget", "rows", "swap", "projects"),
    [
        (None, 3, True, 4),
        (14, 3, True, 4),
        (13, 3, True, 3),  # the projects go first, the smallest and Other before the biggest
        (11, 3, True, 1),
        (10, 3, True, 0),
        (9, 3, False, 0),  # then swap
        (8, 2, False, 0),  # then the plots' rows, both at once
        (7, 2, False, 0),
        (5, 1, False, 0),
        (4, 0, False, 0),  # then the plots
        (3, 0, False, 0),
        (0, 0, False, 0),  # never fewer than the title and the two figures
    ],
)
def test_sheds_to_fit(budget: int | None, rows: int, swap: bool, projects: int) -> None:
    lines = machine_lines(view(), GROUPS, WIDTH, budget)
    text = plain(lines)
    assert sum(is_plot(line) for line in lines) == 2 * rows
    assert ("swap 1.2 / 8.0G" in text) == swap
    assert len(lines) == 3 + 2 * rows + swap + projects
    assert text[0] == "Machine" and text[1].startswith("CPU 38%")
    assert "RAM 22.0 / 32.0G (69%)" in text
    if budget is not None and budget >= 3:
        assert len(lines) <= budget
    if projects:
        assert text[-projects].startswith("api")


def test_swap() -> None:
    assert not any(
        "swap" in t for t in plain(machine_lines(view(swap_total=0, swap_free=0), (), WIDTH))
    )
    full = machine_lines(view(swap_free=0), GROUPS, WIDTH, 4)  # a full swap is never dropped
    assert plain(full) == [
        "Machine",
        plain(full)[1],
        "RAM 22.0 / 32.0G (69%)",
        "swap 8.0 / 8.0G  full",
    ]
    assert full[-1].style == OVER or any(s.style == OVER for s in full[-1].spans)
    assert len(machine_lines(view(swap_free=0), GROUPS, WIDTH, 12)) == 12  # and counted


def test_levels_and_no_cpu_yet() -> None:
    warn = machine_lines(view("warning", available=4.4), (), WIDTH)[5]
    assert warn.plain == "RAM 27.6 / 32.0G (86%)  ⚠ 14% free"
    assert any(s.style == "yellow" for s in warn.spans)
    crit = machine_lines(view("critical", available=1.9, cpu=None, n_cpus=1), (), WIDTH)
    assert crit[1].plain == "CPU –" + " " * 28 + "1 core"
    assert any(s.style == OVER for s in crit[5].spans)
    assert machine_lines(view("critical", available=1.9), (), 20)[5].cell_len <= 20


def test_projects_over_and_long_names() -> None:
    groups = (group("a-project-with-a-very-long-name-indeed", 11, over=True), group("idle", 0))
    lines = machine_lines(view(), groups, WIDTH)
    assert plain(lines)[10:] == ["a-project-with-a-very-long…" + " 11.0G (34%)"]
    assert any(s.style == OVER for s in lines[10].spans)
    assert PLOT_ROWS == 3
