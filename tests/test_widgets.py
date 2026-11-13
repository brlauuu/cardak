import re
from dataclasses import replace
from pathlib import Path

import pytest
from rich.text import Text

from cardak.collectors.machine import MachineSample
from cardak.core.resolver import Kind
from cardak.core.snapshot import AgentView, DockerLine, Group, MachineView, PaneRow
from cardak.ui.widgets import (
    COLUMNS_WIDTH,
    WAITING,
    Row,
    age,
    agent_dot,
    columns,
    docker_row,
    fit_line,
    fmt_count,
    fmt_cpu,
    fmt_rss,
    fmt_usd,
    group_row,
    heading,
    machine_label,
    mcp_row,
    no_match_label,
    outside_row,
    pane_row,
    reason_label,
    unreachable_label,
    waiting_label,
)


def group(kind: Kind = "config", accent: str | None = "#fabd2f") -> Group:
    return Group(
        kind=kind,
        key=Path("/r/w"),
        name="webshop",
        accent=accent,
        path="~/r/w",
        cpu_pct=12.4,
        rss=1_200 * 2**20,
        panes=(),
    )


def cols(row: Row) -> list[str]:
    """The row's columns as words, for comparing."""
    return row[1].plain.split()


def test_formatting() -> None:
    assert fmt_cpu(None) == "–"
    assert fmt_cpu(290.4) == "290%"
    assert fmt_rss(0) == "0M"
    assert fmt_rss(512 * 2**20) == "512M"
    assert fmt_rss(int(1.5 * 2**30)) == "1.5G"


def test_group_labels() -> None:
    left, right = group_row(group())
    assert left.plain == "webshop  ~/r/w" and cols((left, right)) == ["1.2G", "12%"]
    assert right.cell_len == COLUMNS_WIDTH
    assert "not in config" in group_row(group(kind="git"))[0].plain
    assert group_row(group(kind="other", accent=None))[0].plain.startswith("webshop")


def test_group_label_ignores_invalid_accent() -> None:
    text = group_row(group(accent="purpleish"))[0]
    assert text.plain.startswith("webshop")
    assert all("purpleish" not in str(span.style) for span in text.spans)


def test_pane_label() -> None:
    row = PaneRow(
        pane_id=1,
        label="vim",
        detail="notes.md",
        cwd=Path("/r/w"),
        cwd_display=".",
        cpu_pct=None,
        rss=0,
        is_self=True,
    )
    assert pane_row(row)[0].plain == "vim  notes.md  .  (this deck)"
    assert cols(pane_row(row)) == ["0M", "–"]
    assert pane_row(replace(row, over=True, ram_spark="▁█"))[0].plain.startswith("▲ vim")
    assert cols(pane_row(replace(row, ram_spark="▁█"))) == ["0M", "▁█", "–"]


def test_unreachable_label() -> None:
    assert unreachable_label("x").plain == "WezTerm not reachable: x · retrying"


def test_no_match_label() -> None:
    assert no_match_label("[/b]").plain == 'No pane matches "[/b]"'


def claude_row(agent: AgentView | None, pane_id: int = 7) -> PaneRow:
    return PaneRow(
        pane_id=pane_id, label="claude", detail=None, cwd=None, cwd_display=".", cpu_pct=1.0,
        rss=2**20, is_self=False, is_claude=True, agent=agent,
    )  # fmt: skip


def view(status: str, kind: str | None = None, reason: str | None = None,
         tool: str | None = None, summary: str | None = None) -> AgentView:  # fmt: skip
    return AgentView("s", status, kind, 1000.0, reason, tool, "notes",  # type: ignore[arg-type]
                     summary)  # fmt: skip


NOW = 1000.0 + 12 * 60


def test_age() -> None:
    assert [age(s) for s in (0, 59, 180, 7200, 4 * 86400)] == ["<1m", "<1m", "3m", "2h", "4d"]


def test_agent_dot() -> None:
    assert agent_dot(claude_row(view("waiting", "finished"))).plain == "● "
    assert agent_dot(claude_row(view("working"))).plain == "● "
    assert agent_dot(claude_row(view("idle"))).plain == "○ "
    assert agent_dot(claude_row(None)).plain == "· "
    assert agent_dot(replace(claude_row(None), is_claude=False)).plain == ""
    waiting, working = (agent_dot(claude_row(view(s))).style for s in ("waiting", "working"))
    assert waiting != working


def test_pane_label_states() -> None:
    left, right = pane_row(claude_row(view("waiting", "finished", "Done.")), NOW)
    assert left.plain == "● claude  ." and cols((left, right))[:2] == ["wait", "12m"]
    assert any(WAITING in str(s.style) for s in right.spans)
    working = pane_row(claude_row(view("working", tool="Edit src/a.py")), NOW)
    assert working[0].plain.endswith("Edit src/a.py") and cols(working)[0] == "work"
    assert cols(pane_row(claude_row(view("idle")), NOW))[:2] == ["idle", "12m"]
    assert pane_row(claude_row(None), NOW)[0].plain.startswith("· claude")
    assert cols(pane_row(claude_row(None), NOW))[0] == "1M"  # no state column
    shell = replace(claude_row(None), is_claude=False, label="zsh")
    assert pane_row(shell, NOW)[0].plain.startswith("zsh")


def test_reason_label() -> None:
    row = claude_row(view("waiting", "permission", "Bash: uv run pytest\nmore"))
    assert reason_label(row).plain == "    ↳ Approve a tool: Bash: uv run pytest more"
    long = claude_row(view("waiting", "finished", "word " * 80))
    assert len(reason_label(long).plain) <= 4 + 2 + 120


def test_waiting_label() -> None:
    row = claude_row(view("waiting", "question", "Want me to merge?"))
    assert waiting_label(row, NOW).plain == "● notes · claude  12m  Your answer: Want me to merge?"


def test_waiting_group_label() -> None:
    g = replace(group(kind="other"), kind="waiting", name="Waiting on you", path=None)
    assert group_row(g)[0].plain == "Waiting on you" and group_row(g)[1].plain == ""


def test_waiting_label_with_summary() -> None:
    row = claude_row(
        view("waiting", "permission", "Bash: cargo test", summary="Fixed it; wants tests")
    )
    text = waiting_label(row, NOW)
    assert text.plain == "● notes · claude  12m  approve  Fixed it; wants tests"
    assert "cargo test" not in text.plain


@pytest.mark.parametrize(
    ("kind", "tag"), [("permission", "approve"), ("question", "decide"), ("finished", "review")]
)
def test_reason_line_uses_the_tag(kind: str, tag: str) -> None:
    assert reason_label(claude_row(view("waiting", kind, "raw", summary="S"))).plain == (
        f"    ↳ {tag}  S"
    )


def test_summary_line_is_cut_like_a_reason() -> None:
    row = claude_row(view("waiting", "finished", "raw", summary="w" * 200))
    assert len(reason_label(row).plain) <= 4 + 2 + 120


GIB = 2**30


def test_columns_with_spark_and_over() -> None:
    t = columns(None, int(13.9 * GIB), 180.0, total=None, spark="▁▂▄▆█", over=True)
    assert t.plain.split() == ["13.9G", "▁▂▄▆█", "180%"]
    assert any("red" in str(s.style) for s in t.spans)
    plain = columns(None, GIB, None, total=None)
    assert plain.plain.split() == ["1.0G", "–"]
    assert not any("red" in str(s.style) for s in plain.spans)


def test_columns_are_fixed_width_and_right_aligned() -> None:
    t = columns(None, 1_200 * 2**20, 30.0, total=32 * GIB)
    assert t.cell_len == COLUMNS_WIDTH
    assert "1.2G (4%)" in t.plain and t.plain.rstrip().endswith("30%")
    full = columns(None, 1_200 * 2**20, 30.0, total=32 * GIB, ctx=140_000, month=88.0)
    assert full.cell_len == COLUMNS_WIDTH and full.plain.split()[-2:] == ["140k", "$88"]
    # the numbers end at the same cell with and without a graph
    graphed = columns(None, 1_200 * 2**20, 30.0, total=32 * GIB, spark="▂▄▆▆")
    assert graphed.plain.index("(4%)") == t.plain.index("(4%)")


def test_columns_blank_where_none() -> None:
    assert columns(None, None, None, total=None).plain.strip() == ""
    assert columns(None, None, None, total=None).cell_len == COLUMNS_WIDTH


def test_columns_show_share_of_total_ram() -> None:
    def ram(rss: int, total: int) -> str:
        return " ".join(columns(None, rss, None, total=total).plain.split()[:-1])

    assert ram(int(13.9 * GIB), 43 * GIB) == "13.9G (32%)"
    assert ram(412 * 2**20, 43 * GIB) == "412M (1%)"
    assert ram(100 * 2**20, 43 * GIB) == "100M (<1%)"
    assert ram(0, 43 * GIB) == "0M (0%)"
    assert ram(GIB // 2, 100 * GIB) == "512M (1%)"  # half up
    assert ram(5 * GIB // 2, 100 * GIB) == "2.5G (3%)"
    assert ram(GIB, 0) == "1.0G"  # no usable total


def test_heading_names_every_column_in_order() -> None:
    assert heading().plain.split() == ["state", "RAM", "CPU", "ctx", "month"]
    assert heading().cell_len == COLUMNS_WIDTH
    full = columns(
        Text("wait 4m"), GIB, 30.0, total=None, spark="▂▄", ctx=140_000, month=88.0
    ).plain
    # each heading ends where its column's values end (RAM's over the size, not the graph)
    for name in ("state", "RAM", "CPU", "ctx", "month"):
        assert heading().plain.index(name) + len(name) in {
            m.end() for m in re.finditer(r"\S+", full)
        }


def test_fit_line_pads_and_cuts_the_left() -> None:
    row = (Text("cardak  ~/repos/cardak"), columns(None, GIB, 1.0, total=None))
    assert fit_line(row, COLUMNS_WIDTH + 40).cell_len == COLUMNS_WIDTH + 40
    cut = fit_line(row, COLUMNS_WIDTH + 10)
    assert cut.cell_len == COLUMNS_WIDTH + 10 and cut.plain[9] == "…"


def test_fit_line_counts_cells() -> None:
    row = (Text("项目 🚀 build"), columns(None, GIB, 1.0, total=None))
    for width in (COLUMNS_WIDTH + 3, COLUMNS_WIDTH + 8, COLUMNS_WIDTH + 30):
        assert fit_line(row, width).cell_len == width


def test_fit_line_when_columns_fill_the_width() -> None:
    row = (Text("x" * 50), columns(None, GIB, 1.0, total=None))
    assert fit_line(row, COLUMNS_WIDTH - 5).plain == row[1].plain


def test_rows_pass_the_total_on() -> None:
    total = 10 * GIB
    assert cols(group_row(group(), total=total)) == ["1.2G", "(12%)", "12%"]
    r = PaneRow(
        pane_id=1, label="zsh", detail=None, cwd=None, cwd_display=".", cpu_pct=1.0,
        rss=GIB, is_self=False, mcp_rss=GIB, mcp_cpu_pct=1.0,
    )  # fmt: skip
    assert cols(pane_row(r, 0.0, total=total)) == ["1.0G", "(10%)", "1%"]
    assert cols(mcp_row(r, total=total)) == ["1.0G", "(10%)", "1%"]
    line = DockerLine("docker: db", 2 * GIB, 30.0)
    assert cols(docker_row(line, total=total)) == ["2.0G", "(20%)", "30%"]
    other = replace(group(kind="other"), outside_rss=5 * GIB, outside_cpu_pct=2.0)
    assert cols(outside_row(other, total=total)) == ["5.0G", "(50%)", "2%"]


def test_group_row_over_is_red() -> None:
    row = group_row(replace(group(), over=True, ram_spark="▅"))
    assert cols(row) == ["1.2G", "▅", "12%"]
    assert any("red" in str(s.style) for s in row[1].spans)


def test_extra_lines() -> None:
    r = PaneRow(
        pane_id=1, label="claude", detail=None, cwd=None, cwd_display=".", cpu_pct=1.0,
        rss=GIB, is_self=False, mcp_rss=412 * 2**20, mcp_cpu_pct=1.0,
    )  # fmt: skip
    assert mcp_row(r)[0].plain == "    └ MCP servers" and cols(mcp_row(r)) == ["412M", "1%"]
    docker = docker_row(DockerLine("docker: webshop-db-1", 2 * GIB, 30.0))
    assert docker[0].plain == "    docker: webshop-db-1" and cols(docker) == ["2.0G", "30%"]
    other = replace(group(kind="other"), outside_rss=13 * GIB, outside_cpu_pct=200.0)
    outside = outside_row(other)
    assert outside[0].plain == "    outside WezTerm" and cols(outside) == ["13.0G", "200%"]


def machine_view(
    available: float, level: str | None, cpu: float | None = 38.0, swap_free: int = GIB
):
    m = MachineSample(32 * GIB, int(available * GIB), GIB, swap_free, cpu, 16)
    return MachineView(m, level, "▄▅", "▂▃")  # type: ignore[arg-type]


def test_machine_label_levels() -> None:
    assert machine_label(machine_view(10, None)).plain == "CPU 38% ▂▃   RAM 22.0 / 32.0G (69%) ▄▅"
    warn = machine_label(machine_view(4.4, "warning"))
    assert warn.plain == "CPU 38% ▂▃   RAM 27.6 / 32.0G (86%) ▄▅  ⚠ 14% free"
    crit = machine_label(machine_view(1.9, "critical", cpu=None))
    assert crit.plain.startswith("CPU – ▂▃") and crit.plain.endswith("⚠ 6% free")
    assert any("red" in str(s.style) for s in crit.spans)
    assert machine_label(machine_view(10, "critical", swap_free=0)).plain.endswith("swap full")


@pytest.mark.parametrize(
    ("usd", "shown"),
    [
        (0.421, "$0.42"),
        (9.994, "$9.99"),
        (37.4, "$37"),
        (1204.6, "$1,205"),
        (0.0, "$0"),
        (0.004, "<$0.01"),
    ],  # fmt: skip
)
def test_fmt_usd(usd: float, shown: str) -> None:
    assert fmt_usd(usd) == shown


@pytest.mark.parametrize(
    ("n", "shown"),
    [
        (0, "0"),
        (999, "999"),
        (1_234, "1.2k"),
        (340_000, "340k"),
        (1_234_567, "1.2M"),
        (3_400_000_000, "3.4B"),
        (999.5, "1.0k"),  # #115: the edges round up into the next unit, not "1000"
        (9_995, "10k"),
        (999_500, "1.0M"),
        (999_400, "999k"),
        (500.5, "501"),  # half up, as the page's Math.round
        (2_500_000_000_000, "2500B"),
    ],  # fmt: skip
)
def test_fmt_count(n: float, shown: str) -> None:
    assert fmt_count(n) == shown


def test_group_label_spend() -> None:
    assert cols(group_row(replace(group(), spend_usd=37.0))) == ["1.2G", "12%", "$37"]
    for nothing in (None, 0.0):
        assert "$" not in group_row(replace(group(), spend_usd=nothing))[1].plain


def test_agent_row_shows_its_context() -> None:
    row = claude_row(replace(view("idle"), context=312_000))
    assert cols(pane_row(row, NOW)) == ["idle", "12m", "1M", "1%", "312k"]
    assert cols(pane_row(claude_row(view("idle")), NOW)) == ["idle", "12m", "1M", "1%"]


def test_limits_label() -> None:
    from datetime import datetime

    from cardak.core.limits import LimitsView, LimitView
    from cardak.ui.widgets import limits_label

    now = datetime(2026, 10, 30, 12, 0).timestamp()
    five = datetime(2026, 10, 30, 16, 40).timestamp()
    week = datetime(2026, 11, 2, 9, 0).timestamp()
    view = LimitsView(LimitView(27, five, "ok"), LimitView(61, week, "ok"), 30.0)
    text = limits_label(view, now)
    # used, filling up, then the time to the reset and its clock time (#121)
    assert text.plain == "5h ██░░░░░░ 27% · 4h40m (16:40)   week █████░░░ 61% · 2d21h (Mon 09:00)"
    low_view = LimitsView(LimitView(85, five, "low"), LimitView(95, week, "critical"), 0)
    low = limits_label(low_view, now)
    styles = {str(s.style) for s in low.spans}
    assert "yellow" in styles and "bold red" in styles


def test_until() -> None:
    from cardak.ui.widgets import until

    assert [until(s) for s in (5, 60, 59 * 60 + 59, 3600, 2 * 3600 + 10 * 60 + 30)] == [
        "<1m",
        "1m",
        "59m",
        "1h",
        "2h10m",
    ]
    assert [until(s) for s in (86_400, 2 * 86_400 + 21 * 3600 + 59 * 60)] == ["1d", "2d21h"]


def test_limits_label_unknown_and_stale() -> None:
    from cardak.core.limits import LimitsView, LimitView
    from cardak.ui.widgets import limits_label

    unknown = LimitView(None, None, "unknown")
    text = limits_label(LimitsView(unknown, unknown, 25 * 60.0), 0.0)
    assert text.plain == "5h —   week — · read 25 min ago"
