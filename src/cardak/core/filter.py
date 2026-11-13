"""Fuzzy-filter a snapshot for the deck's search line (Textual's command-palette matcher)."""

from dataclasses import dataclass, replace

from textual.fuzzy import Matcher

from cardak.core.snapshot import DeckSnapshot, Group, PaneRow


@dataclass(frozen=True)
class FilterResult:
    snapshot: DeckSnapshot
    best_pane_id: int | None
    n_matches: int


def _haystack(group: Group, row: PaneRow) -> str:
    return " ".join(part for part in (group.name, row.label, row.detail, row.cwd_display) if part)


def filter_snapshot(snap: DeckSnapshot, query: str) -> FilterResult:
    query = query.strip()
    if not query or snap.error:
        return FilterResult(snap, None, snap.n_panes)
    matcher = Matcher(query)
    groups: list[Group] = []
    best: int | None = None
    best_score = 0.0
    for group in snap.groups:
        kept: list[PaneRow] = []
        for row in group.panes:
            score = matcher.match(_haystack(group, row))
            if score <= 0:
                continue
            kept.append(row)
            if score > best_score:  # strict: ties keep the first in tree order
                best, best_score = row.pane_id, score
        if kept:
            groups.append(replace(group, panes=tuple(kept)))
    # a waiting pane is listed twice (waiting group and project); in `w` mode only once
    n = len({r.pane_id for g in groups for r in g.panes})
    return FilterResult(replace(snap, groups=tuple(groups), n_panes=n), best, n)


def waiting_only(snap: DeckSnapshot) -> DeckSnapshot:
    """The deck's `w` view: just the "Waiting on you" group."""
    return replace(snap, groups=tuple(g for g in snap.groups if g.kind == "waiting"))


def waiting_projects(snap: DeckSnapshot) -> DeckSnapshot:
    """`w` in the wide deck (#122), where "Waiting on you" has a pane of its own: each group
    with a waiting session, with just those sessions (no Docker or outside lines)."""
    waiting = {r.pane_id for g in snap.groups if g.kind == "waiting" for r in g.panes}
    groups: list[Group] = []
    for g in snap.groups:
        kept = tuple(r for r in g.panes if r.pane_id in waiting)
        if g.kind != "waiting" and kept:
            groups.append(replace(g, panes=kept, docker=(), outside_rss=None))
    return replace(snap, groups=tuple(groups))
