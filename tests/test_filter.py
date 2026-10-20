from pathlib import Path

from cardak.core.filter import filter_snapshot
from cardak.core.resolver import Kind
from cardak.core.snapshot import DeckSnapshot, Group, PaneRow


def row(pane_id: int, label: str = "zsh", cwd: str = ".", detail: str | None = None) -> PaneRow:
    return PaneRow(
        pane_id=pane_id,
        label=label,
        detail=detail,
        cwd=None,
        cwd_display=cwd,
        cpu_pct=1.0,
        rss=10,
        is_self=False,
    )


def group(kind: Kind, name: str, *rows: PaneRow) -> Group:
    return Group(
        kind=kind,
        key=Path(f"/r/{name}"),
        name=name,
        accent=None,
        path=f"~/r/{name}",
        cpu_pct=50.0,
        rss=999,
        panes=rows,
    )


SNAP = DeckSnapshot(
    groups=(
        group("config", "webshop", row(1, cwd="src"), row(2, label="claude")),
        group("config", "api-gw", row(3, cwd="wiki/eb"), row(4, label="vim", detail="notes.md")),
        group("git", "cardak", row(5)),
        group("other", "Other", row(6, cwd="/tmp")),
    ),
    n_panes=6,
)


def test_blank_query_returns_snapshot_unchanged() -> None:
    for q in ("", "   "):
        result = filter_snapshot(SNAP, q)
        assert result.snapshot is SNAP
        assert (result.best_pane_id, result.n_matches) == (None, 6)


def test_matches_keep_groups_and_order_and_totals() -> None:
    result = filter_snapshot(SNAP, "web")
    names = [(g.name, [r.pane_id for r in g.panes]) for g in result.snapshot.groups]
    assert names == [("webshop", [1, 2]), ("api-gw", [3])]
    assert result.snapshot.groups[0].cpu_pct == 50.0 and result.snapshot.groups[0].rss == 999
    assert result.n_matches == 3
    assert result.snapshot.n_panes == 3


def test_best_match_is_highest_score_first_in_order_on_tie() -> None:
    assert (
        filter_snapshot(SNAP, "web").best_pane_id == 1
    )  # 1 and 2 tie on "webshop…"; 3 scores lower
    assert filter_snapshot(SNAP, "notes").best_pane_id == 4


def test_case_insensitive() -> None:
    assert filter_snapshot(SNAP, "WEB").n_matches == 3


def test_no_match() -> None:
    result = filter_snapshot(SNAP, "zzzz")
    assert result.snapshot.groups == ()
    assert (result.best_pane_id, result.n_matches) == (None, 0)


def test_error_snapshot_passes_through() -> None:
    err = DeckSnapshot(error="no WezTerm GUI socket found")
    assert filter_snapshot(err, "web").snapshot is err
