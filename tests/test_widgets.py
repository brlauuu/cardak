from dataclasses import replace
from pathlib import Path

import pytest

from cardak.collectors.machine import MachineSample
from cardak.core.resolver import Kind
from cardak.core.snapshot import AgentView, DockerLine, Group, MachineView, PaneRow
from cardak.ui.widgets import (
    age,
    agent_dot,
    docker_label,
    fmt_cpu,
    fmt_rss,
    group_label,
    machine_label,
    mcp_label,
    no_match_label,
    outside_label,
    pane_label,
    reason_label,
    unreachable_label,
    usage,
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


def test_formatting() -> None:
    assert fmt_cpu(None) == "–"
    assert fmt_cpu(290.4) == "290%"
    assert fmt_rss(0) == "0M"
    assert fmt_rss(512 * 2**20) == "512M"
    assert fmt_rss(int(1.5 * 2**30)) == "1.5G"


def test_group_labels() -> None:
    assert group_label(group()).plain == "webshop  ~/r/w  RAM 1.2G  CPU 12%"
    assert "not in config" in group_label(group(kind="git")).plain
    assert group_label(group(kind="other", accent=None)).plain.startswith("webshop")


def test_group_label_ignores_invalid_accent() -> None:
    text = group_label(group(accent="purpleish"))
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
    assert pane_label(row).plain == "vim  notes.md  .  RAM 0M  CPU –  (this deck)"
    assert pane_label(replace(row, over=True, ram_spark="▁█")).plain.startswith("▲ vim")
    assert "RAM 0M ▁█  CPU" in pane_label(replace(row, ram_spark="▁█")).plain


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
    waiting = pane_label(claude_row(view("waiting", "finished", "Done.")), NOW).plain
    assert waiting.startswith("● claude") and waiting.endswith("waiting 12m")
    working = pane_label(claude_row(view("working", tool="Edit src/a.py")), NOW).plain
    assert working.endswith("Edit src/a.py")
    assert pane_label(claude_row(view("idle")), NOW).plain.endswith("idle 12m")
    assert pane_label(claude_row(None), NOW).plain.startswith("· claude")
    shell = replace(claude_row(None), is_claude=False, label="zsh")
    assert pane_label(shell, NOW).plain.startswith("zsh")


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
    assert group_label(g).plain == "Waiting on you"


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


def test_usage_with_spark_and_over() -> None:
    t = usage(int(13.9 * GIB), 180.0, "▁▂▄▆█", over=True)
    assert t.plain == "RAM 13.9G ▁▂▄▆█  CPU 180%"
    assert any("red" in str(s.style) for s in t.spans)
    assert usage(GIB, None).plain == "RAM 1.0G  CPU –"
    assert not any("red" in str(s.style) for s in usage(GIB, None).spans)


def test_usage_shows_share_of_total_ram() -> None:
    assert usage(int(13.9 * GIB), 180.0, "▁▂", total=43 * GIB).plain == (
        "RAM 13.9G (32%) ▁▂  CPU 180%"
    )
    assert usage(412 * 2**20, 1.0, total=43 * GIB).plain == "RAM 412M (1%)  CPU 1%"
    assert usage(100 * 2**20, 1.0, total=43 * GIB).plain == "RAM 100M (<1%)  CPU 1%"
    assert usage(0, None, total=43 * GIB).plain == "RAM 0M (0%)  CPU –"
    assert usage(GIB, None, total=0).plain == "RAM 1.0G  CPU –"  # no usable total


def test_labels_pass_the_total_on() -> None:
    total = 10 * GIB
    assert group_label(group(), total=total).plain.endswith("RAM 1.2G (12%)  CPU 12%")
    r = PaneRow(
        pane_id=1, label="zsh", detail=None, cwd=None, cwd_display=".", cpu_pct=1.0,
        rss=GIB, is_self=False, mcp_rss=GIB, mcp_cpu_pct=1.0,
    )  # fmt: skip
    assert "RAM 1.0G (10%)" in pane_label(r, 0.0, total=total).plain
    assert mcp_label(r, total=total).plain.endswith("RAM 1.0G (10%)  CPU 1%")
    line = DockerLine("docker: db", 2 * GIB, 30.0)
    assert docker_label(line, total=total).plain.endswith("RAM 2.0G (20%)  CPU 30%")
    other = replace(group(kind="other"), outside_rss=5 * GIB, outside_cpu_pct=2.0)
    assert outside_label(other, total=total).plain.endswith("RAM 5.0G (50%)  CPU 2%")


def test_group_label_over_is_red() -> None:
    text = group_label(replace(group(), over=True, ram_spark="▅"))
    assert text.plain == "webshop  ~/r/w  RAM 1.2G ▅  CPU 12%"
    assert any("red" in str(s.style) for s in text.spans)


def test_extra_lines() -> None:
    r = PaneRow(
        pane_id=1, label="claude", detail=None, cwd=None, cwd_display=".", cpu_pct=1.0,
        rss=GIB, is_self=False, mcp_rss=412 * 2**20, mcp_cpu_pct=1.0,
    )  # fmt: skip
    assert mcp_label(r).plain == "    └ MCP servers  RAM 412M  CPU 1%"
    assert (
        docker_label(DockerLine("docker: webshop-db-1", 2 * GIB, 30.0)).plain
        == "    docker: webshop-db-1  RAM 2.0G  CPU 30%"
    )
    other = replace(group(kind="other"), outside_rss=13 * GIB, outside_cpu_pct=200.0)
    assert outside_label(other).plain == "    outside WezTerm  RAM 13.0G  CPU 200%"


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
