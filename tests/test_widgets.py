from pathlib import Path

from cardak.core.resolver import Kind
from cardak.core.snapshot import Group, PaneRow
from cardak.ui.widgets import (
    fmt_cpu,
    fmt_rss,
    group_label,
    no_match_label,
    pane_label,
    unreachable_label,
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
