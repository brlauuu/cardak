"""Claude Code hook payloads (stdin JSON of `cardak hook`). The only reader of their fields.

Not a stable API, so parse defensively: anything malformed gives None or a default,
never an exception. Field names are recorded in tests/fixtures/hooks/.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class HookEvent:
    name: str  # hook_event_name, verbatim
    session_id: str
    cwd: Path | None = None
    transcript_path: Path | None = None
    source: str | None = None  # SessionStart
    prompt: str | None = None  # UserPromptSubmit
    tool_name: str | None = None
    tool_input: Mapping[str, object] = field(default_factory=dict)
    notification_type: str | None = None
    message: str | None = None  # Notification
    last_assistant_message: str | None = None  # Stop
    reason: str | None = None  # SessionEnd


def _str(data: Mapping[str, object], key: str) -> str | None:
    value = data.get(key)
    return value if isinstance(value, str) else None


def _path(data: Mapping[str, object], key: str) -> Path | None:
    value = _str(data, key)
    return Path(value) if value else None


def parse(text: str) -> HookEvent | None:
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    name, session_id = _str(data, "hook_event_name"), _str(data, "session_id")
    if not name or not session_id:
        return None
    tool_input = data.get("tool_input")
    return HookEvent(
        name=name,
        session_id=session_id,
        cwd=_path(data, "cwd"),
        transcript_path=_path(data, "transcript_path"),
        source=_str(data, "source"),
        prompt=_str(data, "prompt"),
        tool_name=_str(data, "tool_name"),
        tool_input=tool_input if isinstance(tool_input, dict) else {},
        notification_type=_str(data, "notification_type"),
        message=_str(data, "message"),
        last_assistant_message=_str(data, "last_assistant_message"),
        reason=_str(data, "reason"),
    )
