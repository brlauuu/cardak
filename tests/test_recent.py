from dataclasses import replace
from pathlib import Path

from cardak.collectors.machine import MachineSample
from cardak.core.config import ResourceSettings
from cardak.core.recent import Recent, decorate, spark
from cardak.core.resources import derive
from cardak.core.snapshot import DeckSnapshot, Group, PaneRow

GIB = 2**30
T32 = derive(ResourceSettings(), 32 * GIB, 16)
WEBSHOP = Path("/home/user/repos/webshop")


def row(pane_id: int, rss: int = GIB, cpu: float | None = 10.0, mcp: int = 0) -> PaneRow:
    return PaneRow(
        pane_id=pane_id,
        label="zsh",
        detail=None,
        cwd=None,
        cwd_display=".",
        cpu_pct=cpu,
        rss=rss,
        is_self=False,
        mcp_rss=mcp,
    )


def group(kind: str, key: Path | None, *rows: PaneRow, rss: int | None = None) -> Group:
    return Group(
        kind=kind,  # type: ignore[arg-type]
        key=key,
        name="x",
        accent=None,
        path=None,
        cpu_pct=sum(r.cpu_pct or 0.0 for r in rows),
        rss=sum(r.rss + r.mcp_rss for r in rows) if rss is None else rss,
        panes=rows,
    )


def machine(available_gib: float) -> MachineSample:
    return MachineSample(32 * GIB, int(available_gib * GIB), GIB, GIB, 40.0, 16)


def test_spark_scales_to_top() -> None:
    assert spark([0, 4, 8], top=8, width=8) == "▁▅█"
    assert spark([None, None], top=8) == "  "
    assert spark([], top=8) == ""
    assert spark([1.0], top=0) == ""
    assert spark([100.0], top=8) == "█"  # over the top stays the top bar


def test_spark_buckets_take_the_max() -> None:
    assert spark([1, 8, 1, 1], top=8, width=2) == "█▂"
    assert spark([None, 8, None, None], top=8, width=2) == "█ "


def test_spark_keeps_the_newest_on_the_right() -> None:
    assert spark([8, 0, 0], top=8, width=2) == "█▁"  # buckets cut from the right: [8] [0, 0]


def test_recent_keeps_a_window_and_forgets_closed_panes() -> None:
    recent = Recent(keep=3)
    snap = DeckSnapshot(groups=(group("config", WEBSHOP, row(1)),))
    for _ in range(5):
        recent.record(snap)
    assert recent.ram("p:1") == [GIB, GIB, GIB]
    assert recent.cpu("p:1") == [10.0, 10.0, 10.0]
    assert len(recent.ram(f"g:config:{WEBSHOP}")) == 3
    recent.record(DeckSnapshot(groups=(group("config", WEBSHOP),)))
    assert recent.ram("p:1") == []


def test_decorate_marks_over_and_spares_other() -> None:
    big = row(1, rss=9 * GIB)
    snap = DeckSnapshot(
        groups=(
            group("waiting", None, big),
            group("config", WEBSHOP, big, row(2)),
            group("other", None, row(3), rss=14 * GIB),
        )
    )
    recent = Recent()
    recent.record(snap)
    out = decorate(snap, recent, T32)
    waiting, webshop, other = out.groups
    assert webshop.over and webshop.panes[0].over and not webshop.panes[1].over
    assert not other.over and not waiting.over
    assert waiting.panes[0].over and waiting.panes[0].ram_spark == webshop.panes[0].ram_spark
    assert webshop.panes[0].ram_spark == "█" and webshop.ram_spark == "█"


def test_pane_mark_and_graph_follow_its_own_number() -> None:
    # the row prints the work only (MCP has its own line); the project total includes MCP
    claude = row(1, rss=5 * GIB, mcp=4 * GIB)
    snap = DeckSnapshot(groups=(group("config", WEBSHOP, claude),))
    recent = Recent()
    recent.record(snap)
    out = decorate(snap, recent, T32).groups[0]
    assert not out.panes[0].over and out.over
    assert recent.ram("p:1") == [5 * GIB]


def test_machine_view_levels() -> None:
    recent = Recent()
    snap = replace(DeckSnapshot(), machine=machine(4))
    recent.record(snap)
    view = decorate(snap, recent, T32).machine_view
    assert view is not None and view.level == "warning"
    assert view.ram_spark == "█" and view.cpu_spark == "▄"  # 28 of 32 GiB used; 40% CPU
    assert decorate(DeckSnapshot(), recent, T32).machine_view is None


def test_other_graph_is_scaled_to_the_machine() -> None:
    # Other holds the desktop and browsers, often over the pane threshold: against 32 GiB it
    # reads as a level, not a solid bar
    other = group("other", None, rss=14 * GIB)
    snap = replace(DeckSnapshot(groups=(other,)), machine=machine(10))
    recent = Recent()
    recent.record(snap)
    assert decorate(snap, recent, T32).groups[0].ram_spark == "▄"  # 14/32 × 8 = 3.5
