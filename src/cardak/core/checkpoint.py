"""Whether the deck may type `/checkpoint` into an agent pane, and which one. Pure.

Only a session at its input box takes it: text sent to a permission prompt, an
AskUserQuestion menu or an MCP dialog would answer it. Every wait but a finished turn leaves
`current_tool` set (`Stop`, `UserPromptSubmit` and `SessionStart` clear it), so a session with a
current tool is never at its input box.
"""

from typing import Protocol

from cardak.core.agents import SessionState, Status, WaitKind
from cardak.core.snapshot import Group, PaneRow

TEXT = "/checkpoint"
ENTER = "\r"  # its own write: in one chunk with the text, a TUI can take it as pasted input
ENTER_DELAY_S = 0.15
GONE = "That session has ended or moved; nothing sent."
PROJECT_KINDS = ("config", "git")
NOT_CLAUDE = "Select a Claude session."


class Session(Protocol):  # an AgentView from the snapshot, or a SessionState from the store
    @property
    def status(self) -> Status: ...
    @property
    def kind(self) -> WaitKind | None: ...
    @property
    def current_tool(self) -> str | None: ...


def refusal(agent: Session | None) -> str | None:
    """Why this session can't take a checkpoint now; None when it can."""
    if agent is None:
        return "No status for this session (run `cardak hooks install`, then start a new session)."
    if agent.status == "working":
        return "Claude is working; checkpoint when it's done."
    if agent.kind == "permission":
        return "Claude is waiting for a permission answer."
    if agent.current_tool is not None:  # a menu or dialog, still open after the user has seen it
        return "Claude is showing a question; answer it first."
    return None


def target(group: Group, row: PaneRow | None) -> PaneRow | str:
    """The pane to send to, or the reason there is none."""
    if row is None:
        if group.kind not in PROJECT_KINDS:
            return NOT_CLAUDE
        claude = [r for r in group.panes if r.is_claude]
        if not claude:
            return f"No Claude session in {group.name}."
        if len(claude) > 1:
            return f"Several Claude sessions in {group.name}: select one."
        row = claude[0]
    if not row.is_claude:
        return NOT_CLAUDE
    return refusal(row.agent) or row


def recheck(state: SessionState | None, pane_id: int) -> str | None:
    """The same decision on the stored state when the user confirms: it may have moved on."""
    if state is None or state.ended_at is not None or state.pane_id != pane_id:
        return GONE
    return refusal(state)
