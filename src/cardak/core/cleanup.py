"""Where `x` opens a tab to run `/repo-cleanup` in a project, and what it types there. Pure.

The tab is a plain shell in the project's directory; `claude /repo-cleanup` is typed into it,
so Claude starts with the user's own PATH and shell setup and the tab stays open after it.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from cardak.core.checkpoint import PROJECT_KINDS
from cardak.core.snapshot import Group, PaneRow

COMMAND = "claude /repo-cleanup"
NOT_A_PROJECT = "Select a project or one of its panes."


@dataclass(frozen=True)
class Place:
    cwd: Path
    near: int | None  # the new tab goes in this pane's window; None: the deck's own


def _home(pane_id: int, groups: Sequence[Group]) -> Group | None:
    """The project group a pane is listed in, besides "Waiting on you"."""
    for g in groups:
        if g.kind != "waiting" and any(r.pane_id == pane_id for r in g.panes):
            return g
    return None


def place(group: Group, row: PaneRow | None, groups: Sequence[Group] = ()) -> Place | str:
    """Where the tab opens, or why it can't. A row under "Waiting on you" opens in its project,
    found among groups."""
    if group.kind == "waiting" and row is not None:
        group = _home(row.pane_id, groups) or group
    if group.kind not in PROJECT_KINDS or group.key is None:
        return NOT_A_PROJECT
    near = row or (group.panes[0] if group.panes else None)
    return Place(group.key, near.pane_id if near else None)
