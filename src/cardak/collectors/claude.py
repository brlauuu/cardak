"""The only reader of ~/.claude/. M1: session metadata by pid.

~/.claude/sessions/<pid>.json is written after a session's first prompt; before that
only a <pid>.<hash>.key file exists. Not a stable API, so parse defensively.
"""

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ClaudeSession:
    session_id: str
    name: str | None
    cwd: Path | None


def session_meta(pid: int, sessions_dir: Path | None = None) -> ClaudeSession | None:
    directory = sessions_dir or Path.home() / ".claude" / "sessions"
    try:
        data = json.loads((directory / f"{pid}.json").read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("sessionId"), str):
        return None
    name, cwd = data.get("name"), data.get("cwd")
    return ClaudeSession(
        session_id=data["sessionId"],
        name=name if isinstance(name, str) else None,
        cwd=Path(cwd) if isinstance(cwd, str) else None,
    )
