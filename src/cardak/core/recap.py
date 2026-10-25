"""What the recap view shows: the handoff notes for a project or pane, and git since the latest.

Each worktree keeps its own note, so a project row gathers the note at the project's top level
and at each of its panes' top levels, newest first.
"""

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from cardak.collectors.git import GitState, Runner, state, toplevel
from cardak.collectors.handoff import Entry, read_note
from cardak.core.snapshot import Group, PaneRow

PROJECT_KINDS = ("config", "git")
COMMITS = 20


@dataclass(frozen=True)
class Note:
    top: Path
    entry: Entry | None  # the latest; None: no note there


def dirs_for(group: Group, row: PaneRow | None) -> list[Path] | str:
    """The directories to look in, or why there are none."""
    if row is not None:
        return [row.cwd] if row.cwd is not None else "This pane has no directory."
    if group.kind not in PROJECT_KINDS or group.key is None:
        return "Select a project or a pane."
    return [group.key, *(r.cwd for r in group.panes if r.cwd is not None)]


def notes(dirs: Sequence[Path], runner: Runner = subprocess.run) -> list[Note]:
    """One Note per top level: those with an entry newest first, then the rest in order."""
    tops: list[Path] = []
    for d in dirs:
        top = toplevel(d, runner) or d
        if top not in tops:
            tops.append(top)
    found = [Note(top, (read_note(top) or [None])[-1]) for top in tops]
    dated = [(n.entry.when, n) for n in found if n.entry is not None]
    dated.sort(key=lambda pair: pair[0], reverse=True)
    return [n for _, n in dated] + [n for n in found if n.entry is None]


def since(note: Note, runner: Runner = subprocess.run) -> GitState | None:
    when = note.entry.when.timestamp() if note.entry is not None else None
    return state(note.top, when, COMMITS, runner)


def _plural(n: int, unit: str) -> str:
    return f"{n} {unit}{'' if n == 1 else 's'} ago"


def ago(seconds: float) -> str:
    if seconds < 60:
        return "just now"  # a clock that ran ahead lands here too
    if seconds < 3600:
        return _plural(int(seconds // 60), "minute")
    if seconds < 86_400:
        return _plural(int(seconds // 3600), "hour")
    return _plural(int(seconds // 86_400), "day")
