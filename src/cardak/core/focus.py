"""The focus history cardak.lua (module 3) writes: which pane the user looked at, and when."""

from collections.abc import Mapping, Sequence
from pathlib import Path

from cardak.core.agents import FocusChange
from cardak.core.config import state_dir

FOCUS_HEADER = "# cardak-focus: 1"
HISTORY = 64


def focus_path(env: Mapping[str, str] | None = None) -> Path:
    return state_dir(env) / "focus"


def read_history(path: Path) -> list[FocusChange]:
    """Oldest first; [] when the file is missing, unreadable or another format version."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    if not lines or lines[0] != FOCUS_HEADER:
        return []
    history = []
    for line in lines[1:]:
        pane, _, ms = line.partition(" ")
        if not ms.isdigit() or not (pane == "none" or pane.isdigit()):
            continue
        history.append(FocusChange(None if pane == "none" else int(pane), int(ms) / 1000))
    return history[-HISTORY:]


def focused_pane(history: Sequence[FocusChange]) -> int | None:
    return history[-1].pane_id if history else None
