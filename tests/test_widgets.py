from dataclasses import replace
from pathlib import Path

import pytest

from cardak.core.resolver import Kind
from cardak.core.snapshot import AgentView, Group, PaneRow
from cardak.ui.widgets import (
    age,
    agent_dot,
    fmt_cpu,
    fmt_rss,
    group_label,
    no_match_label,
    pane_label,
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


def test_formatting() -> None:
    assert fmt_cpu(None) == "–"
    assert fmt_cpu(290.4) == "290%"
    assert fmt_rss(0) == "0M"
    assert fmt_rss(512 * 2**20) == "512M"
    assert fmt_rss(int(1.5 * 2**30)) == "1.5G"


def test_group_labels() -> None:
    assert group_label(group()).plain == "webshop  ~/r/w  CPU 12%  RAM 1.2G"
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
    assert pane_label(row).plain == "vim  notes.md  .  CPU –  RAM 0M  (this deck)"


def test_unreachable_label() -> None:
    assert unreachable_label("x").plain == "WezTerm not reachable: x — retrying"


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
