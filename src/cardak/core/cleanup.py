"""Where `x` opens a tab to run `/repo-cleanup` in a project, and what it types there. Pure.

The tab is a plain shell in the project's directory; `claude /repo-cleanup` is typed into it,
so Claude starts with the user's own PATH and shell setup and the tab stays open after it.
"""

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


def place(group: Group, row: PaneRow | None) -> Place | str:
    """Where the tab opens, or why it can't."""
    if group.kind not in PROJECT_KINDS or group.key is None:
        return NOT_A_PROJECT
    near = row or (group.panes[0] if group.panes else None)
    return Place(group.key, near.pane_id if near else None)
