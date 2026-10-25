from pathlib import Path

import pytest

from cardak.collectors.hooks import HookEvent
from cardak.core.agents import Status, WaitKind, apply, mark_seen
from cardak.core.checkpoint import COMMAND, refusal, target
from cardak.core.snapshot import AgentView, Group, PaneRow

ASK = {"questions": [{"question": "Tea or coffee?", "header": "Drink"}]}
NO_HOOKS = "No status for this session (run `cardak hooks install`, then start a new session)."
WORKING = "Claude is working; checkpoint when it's done."
PERMISSION = "Claude is waiting for a permission answer."
MENU = "Claude is showing a question; answer it first."


def agent(
    status: Status = "idle", kind: WaitKind | None = None, tool: str | None = None
) -> AgentView:
    return AgentView(
        session_id="s1", status=status, kind=kind, since=0.0, reason=None, current_tool=tool,
        project="webshop",
    )  # fmt: skip


def row(pane_id: int, claude: bool = True, a: AgentView | None = None) -> PaneRow:
    return PaneRow(
        pane_id=pane_id, label="claude" if claude else "zsh", detail=None, cwd=None,
        cwd_display=".", cpu_pct=None, rss=0, is_self=False, is_claude=claude,
        agent=a if a is not None or not claude else agent(),
    )  # fmt: skip


def group(kind: str, *rows: PaneRow) -> Group:
    return Group(
        kind=kind, key=Path("/home/user/repos/webshop"), name="webshop", accent=None,  # type: ignore[arg-type]
        path=None, cpu_pct=0.0, rss=0, panes=rows,
    )  # fmt: skip


def test_command_is_slash_checkpoint_and_enter() -> None:
    assert COMMAND == "/checkpoint\r"


@pytest.mark.parametrize(
    ("a", "expected"),
    [
        (None, NO_HOOKS),
        (agent("working"), WORKING),
        (agent("waiting", "permission"), PERMISSION),
        (agent("waiting", "question", "AskUserQuestion"), MENU),
        (agent("idle", None, "AskUserQuestion"), MENU),
        (agent("idle"), None),
        (agent("waiting", "finished"), None),
        (agent("waiting", "question"), None),
    ],
)
def test_refusal_table(a: AgentView | None, expected: str | None) -> None:
    assert refusal(a) == expected


def test_seen_menu_still_refused() -> None:
    s, _ = apply(
        None,
        HookEvent(name="PreToolUse", session_id="s1", tool_name="AskUserQuestion", tool_input=ASK),
        7,
        1.0,
    )  # type: ignore[arg-type]
    s = mark_seen(s, 2.0)
    assert s.status == "idle"
    view = agent(s.status, s.kind, s.current_tool)
    assert refusal(view) == MENU


def test_target_pane() -> None:
    r = row(12)
    assert target(group("config", r), r) is r


def test_target_pane_not_claude() -> None:
    r = row(13, claude=False)
    assert target(group("config", r), r) == "Select a Claude session."


def test_target_pane_refused() -> None:
    r = row(12, a=agent("working"))
    assert target(group("config", r), r) == WORKING


def test_target_project_with_one() -> None:
    r = row(12)
    assert target(group("git", row(13, claude=False), r), None) is r


def test_target_project_with_none() -> None:
    assert target(group("config", row(13, claude=False)), None) == "No Claude session in webshop."


def test_target_project_with_several() -> None:
    g = group("config", row(12), row(14))
    assert target(g, None) == "Several Claude sessions in webshop: select one."


@pytest.mark.parametrize("kind", ["waiting", "other"])
def test_target_header_of_waiting_or_other(kind: str) -> None:
    assert target(group(kind, row(12)), None) == "Select a Claude session."


def test_target_claude_without_hook_record() -> None:
    r = PaneRow(
        pane_id=12, label="claude", detail=None, cwd=None, cwd_display=".", cpu_pct=None, rss=0,
        is_self=False, is_claude=True, agent=None,
    )  # fmt: skip
    assert target(group("config", r), r) == NO_HOOKS
