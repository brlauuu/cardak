"""Whether the deck may type `/checkpoint` into an agent pane, and which one. Pure.

Only a session at its input box takes it: text sent to a permission prompt or an
AskUserQuestion menu would answer it.
"""

from cardak.core.agents import ASK_TOOL
from cardak.core.snapshot import AgentView, Group, PaneRow

COMMAND = "/checkpoint\r"
PROJECT_KINDS = ("config", "git")
NOT_CLAUDE = "Select a Claude session."


def refusal(agent: AgentView | None) -> str | None:
    """Why this session can't take a checkpoint now; None when it can."""
    if agent is None:
        return "No status for this session (run `cardak hooks install`, then start a new session)."
    if agent.current_tool == ASK_TOOL:  # a menu stays open after the user has seen it
        return "Claude is showing a question; answer it first."
    if agent.status == "working":
        return "Claude is working; checkpoint when it's done."
    if agent.kind == "permission":
        return "Claude is waiting for a permission answer."
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
