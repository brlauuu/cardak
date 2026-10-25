"""Claude Code session status from hook events. Pure: no I/O.

working: Claude is doing something. waiting: it needs the user, who hasn't looked since.
idle: nothing new for the user. The transitions are the spec's table (M4 design).
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from cardak.collectors.hooks import HookEvent

Status = Literal["working", "waiting", "idle"]
WaitKind = Literal["permission", "question", "finished"]

KIND_LABELS: dict[WaitKind, str] = {
    "permission": "Approve a tool",
    "question": "Your answer",
    "finished": "Turn finished",
}

EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
READ_TOOLS = frozenset({"Read", "Grep", "Glob", "LS", "WebFetch", "WebSearch"})
COMMAND_TOOLS = frozenset({"Bash"})
QUESTION_NOTIFICATIONS = frozenset(
    {"elicitation_dialog", "elicitation_url_dialog", "agent_needs_input"}
)
ASK_TOOL = "AskUserQuestion"
MAX_TURN_FILES = 50
MAX_SUMMARY = 80


@dataclass(frozen=True)
class SessionState:
    session_id: str
    pane_id: int | None = None
    cwd: Path | None = None
    transcript_path: Path | None = None
    status: Status = "idle"
    kind: WaitKind | None = None
    status_since: float = 0.0
    reason: str | None = None
    last_prompt: str | None = None
    last_prompt_at: float | None = None
    last_message: str | None = None
    current_tool: str | None = None
    turn_edits: int = 0
    turn_reads: int = 0
    turn_commands: int = 0
    turn_other: int = 0
    turn_files: tuple[str, ...] = ()
    permission_requested: bool = False  # a PermissionRequest since the last PreToolUse
    seen_at: float | None = None
    started_at: float = 0.0
    updated_at: float = 0.0
    ended_at: float | None = None


@dataclass(frozen=True)
class Ping:
    session_id: str
    pane_id: int | None
    cwd: Path | None
    kind: WaitKind
    reason: str


@dataclass(frozen=True)
class FocusChange:
    pane_id: int | None  # None: no WezTerm window focused
    at: float  # seconds since the epoch


def _cut(text: str, limit: int = MAX_SUMMARY) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _first_question(tool_input: Mapping[str, object]) -> str | None:
    questions = tool_input.get("questions")
    if isinstance(questions, list) and questions and isinstance(questions[0], dict):
        text = questions[0].get("question")
        if isinstance(text, str):
            return text
    return None


def tool_summary(tool_name: str, tool_input: Mapping[str, object]) -> str:
    def arg(*keys: str) -> str | None:
        for key in keys:
            value = tool_input.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip().splitlines()[0]
        return None

    if tool_name.startswith("mcp__") and tool_name.count("__") >= 2:
        _, server, tool = tool_name.split("__", 2)
        return _cut(f"{server}: {tool}")
    if tool_name == ASK_TOOL:
        return _cut(_first_question(tool_input) or tool_name)
    if tool_name == "Bash" and (command := arg("command")):
        return _cut(f"Bash: {command}")
    if tool_name in ("Task", "Agent") and (description := arg("description")):
        return _cut(f"{tool_name}: {description}")
    target = arg("file_path", "notebook_path", "path", "pattern", "url", "query")
    return _cut(f"{tool_name} {target}" if target else tool_name)


def ends_with_question(text: str) -> bool:
    return text.rstrip().rstrip("*_`\"')").endswith("?")


def _set_status(
    state: SessionState, status: Status, kind: WaitKind | None, reason: str | None, now: float
) -> SessionState:
    changed = (status, kind) != (state.status, state.kind)
    return replace(
        state,
        status=status,
        kind=kind,
        reason=reason,
        status_since=now if changed else state.status_since,
    )


def _counted(state: SessionState, tool: str, tool_input: Mapping[str, object]) -> SessionState:
    if tool in EDIT_TOOLS:
        path = tool_input.get("file_path") or tool_input.get("notebook_path")
        files = state.turn_files
        if isinstance(path, str) and path not in files and len(files) < MAX_TURN_FILES:
            files = (*files, path)
        return replace(state, turn_edits=state.turn_edits + 1, turn_files=files)
    if tool in READ_TOOLS:
        return replace(state, turn_reads=state.turn_reads + 1)
    if tool in COMMAND_TOOLS:
        return replace(state, turn_commands=state.turn_commands + 1)
    return replace(state, turn_other=state.turn_other + 1)


Handler = Callable[[SessionState, HookEvent, float], SessionState]


def _on_session_start(s: SessionState, e: HookEvent, now: float) -> SessionState:
    # `--resume` keeps the session id, so a session that ended can start again
    if e.source == "compact":  # auto-compaction, mid-turn or mid-wait: nothing has changed
        return replace(s, ended_at=None)
    return _set_status(replace(s, ended_at=None, current_tool=None), "idle", None, None, now)


def _on_prompt(s: SessionState, e: HookEvent, now: float) -> SessionState:
    s = replace(
        s, last_prompt=e.prompt, last_prompt_at=now, current_tool=None,
        turn_edits=0, turn_reads=0, turn_commands=0, turn_other=0, turn_files=(),
        permission_requested=False,
    )  # fmt: skip
    return _set_status(s, "working", None, None, now)


def _on_pre_tool(s: SessionState, e: HookEvent, now: float) -> SessionState:
    tool = e.tool_name or ""
    s = replace(s, permission_requested=False)
    if tool == ASK_TOOL:  # current_tool marks the open menu, so the deck won't type into it
        s = replace(s, current_tool=ASK_TOOL)
        return _set_status(s, "waiting", "question", tool_summary(tool, e.tool_input), now)
    s = replace(s, current_tool=tool_summary(tool, e.tool_input))
    return _set_status(s, "working", None, None, now)


def _on_post_tool(s: SessionState, e: HookEvent, now: float) -> SessionState:
    tool = e.tool_name or ""
    s = _counted(replace(s, current_tool=tool_summary(tool, e.tool_input)), tool, e.tool_input)
    return _set_status(s, "working", None, None, now)


def _on_permission(s: SessionState, e: HookEvent, now: float) -> SessionState:
    tool = e.tool_name or ""
    s = replace(s, permission_requested=True)
    if tool != ASK_TOOL:
        s = replace(s, current_tool=s.current_tool or tool_summary(tool, e.tool_input))
        return _set_status(s, "waiting", "permission", tool_summary(tool, e.tool_input), now)
    s = replace(s, current_tool=ASK_TOOL)  # the menu is open (its PreToolUse may be missing)
    if (s.status, s.kind) == ("waiting", "question"):
        return s  # AskUserQuestion asks permission too; it is already waiting
    return _set_status(s, "waiting", "question", tool_summary(tool, e.tool_input), now)


def _on_notification(s: SessionState, e: HookEvent, now: float) -> SessionState:
    if e.notification_type == "permission_prompt":
        if s.permission_requested:
            return s  # the PermissionRequest for this prompt already counted
        reason = s.current_tool or e.message or KIND_LABELS["permission"]
        return _set_status(s, "waiting", "permission", reason, now)
    if e.notification_type in QUESTION_NOTIFICATIONS:  # a dialog is open: mark it like a menu
        s = replace(s, current_tool=s.current_tool or e.notification_type)
        return _set_status(s, "waiting", "question", e.message, now)
    return s  # idle_prompt and the rest change nothing


def _on_stop(s: SessionState, e: HookEvent, now: float) -> SessionState:
    message = e.last_assistant_message or ""
    kind: WaitKind = "question" if ends_with_question(message) else "finished"
    s = replace(s, last_message=e.last_assistant_message, current_tool=None)
    return _set_status(s, "waiting", kind, e.last_assistant_message, now)


def _on_session_end(s: SessionState, e: HookEvent, now: float) -> SessionState:
    return replace(s, ended_at=now)


# Anything else (SubagentStop, events newer than this table) leaves the state as it is.
HANDLERS: dict[str, Handler] = {
    "SessionStart": _on_session_start,
    "UserPromptSubmit": _on_prompt,
    "PreToolUse": _on_pre_tool,
    "PostToolUse": _on_post_tool,
    "PostToolUseFailure": _on_post_tool,
    "PermissionRequest": _on_permission,
    "Notification": _on_notification,
    "Stop": _on_stop,
    "SessionEnd": _on_session_end,
}
# Registered but deliberately unhandled (an internal agent finishing); anything else is new.
IGNORED_EVENTS = frozenset({"SubagentStop"})


def apply(
    state: SessionState | None, event: HookEvent, pane_id: int | None, now: float
) -> tuple[SessionState, Ping | None]:
    old = state or SessionState(event.session_id, status_since=now, started_at=now)
    handler = HANDLERS.get(event.name)
    new = handler(old, event, now) if handler else old
    new = replace(
        new,
        updated_at=now,
        pane_id=pane_id if pane_id is not None else new.pane_id,
        cwd=event.cwd or new.cwd,
        transcript_path=event.transcript_path or new.transcript_path,
    )
    ping = None
    if (
        new.status == "waiting"
        and new.kind is not None
        and (state is None or old.status != "waiting")
    ):
        ping = Ping(new.session_id, new.pane_id, new.cwd, new.kind, new.reason or "")
    return new, ping


def mark_seen(state: SessionState, now: float) -> SessionState:
    """The user looked at a waiting session: a permission prompt is presumed answered."""
    if state.status != "waiting":
        return state
    if state.kind == "permission":
        state = replace(state, current_tool=state.reason)
        return replace(_set_status(state, "working", None, None, now), seen_at=now)
    return replace(_set_status(state, "idle", None, None, now), seen_at=now)


def seen_by_focus(
    state: SessionState, history: Sequence[FocusChange], now: float, dwell: float = 1.0
) -> bool:
    """Was the session's pane focused for `dwell` seconds since it started waiting?"""
    if state.status != "waiting" or state.pane_id is None:
        return False
    for i, change in enumerate(history):
        if change.pane_id != state.pane_id:
            continue
        end = history[i + 1].at if i + 1 < len(history) else now
        if end - max(change.at, state.status_since) >= dwell:
            return True
    return False
