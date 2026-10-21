from pathlib import Path

import pytest

from cardak.collectors.hooks import HookEvent
from cardak.core.agents import (
    FocusChange,
    SessionState,
    apply,
    ends_with_question,
    mark_seen,
    seen_by_focus,
    tool_summary,
)

SID = "s1"


def ev(name: str, **kw: object) -> HookEvent:
    return HookEvent(name=name, session_id=SID, **kw)  # type: ignore[arg-type]


BASH = {"command": "uv run pytest tests/core -x", "description": "Run tests"}
ASK = {"questions": [{"question": "Do you prefer tea or coffee?", "header": "Drink"}]}


def run(*events: HookEvent, start: SessionState | None = None) -> tuple[SessionState, list]:
    state, pings = start, []
    for i, e in enumerate(events):
        state, ping = apply(state, e, 7, 100.0 + i)
        if ping:
            pings.append(ping)
    assert state is not None
    return state, pings


@pytest.mark.parametrize(
    ("events", "status", "kind", "reason"),
    [
        ([ev("SessionStart", source="startup")], "idle", None, None),
        ([ev("UserPromptSubmit", prompt="hi")], "working", None, None),
        ([ev("PreToolUse", tool_name="Bash", tool_input=BASH)], "working", None, None),
        ([ev("PostToolUse", tool_name="Bash", tool_input=BASH)], "working", None, None),
        ([ev("PostToolUseFailure", tool_name="Bash", tool_input=BASH)], "working", None, None),
        (
            [
                ev("PreToolUse", tool_name="Bash", tool_input=BASH),
                ev("PermissionRequest", tool_name="Bash", tool_input=BASH),
            ],
            "waiting",
            "permission",
            "Bash: uv run pytest tests/core -x",
        ),
        (
            [
                ev("PreToolUse", tool_name="Bash", tool_input=BASH),
                ev(
                    "Notification",
                    notification_type="permission_prompt",
                    message="Claude needs your permission",
                ),
            ],
            "waiting",
            "permission",
            "Bash: uv run pytest tests/core -x",
        ),
        (
            [ev("Notification", notification_type="elicitation_dialog", message="Pick a server")],
            "waiting",
            "question",
            "Pick a server",
        ),
        (
            [ev("Notification", notification_type="agent_needs_input", message="Input needed")],
            "waiting",
            "question",
            "Input needed",
        ),
        (
            [
                ev("UserPromptSubmit", prompt="hi"),
                ev("Notification", notification_type="idle_prompt", message="waiting"),
            ],
            "working",
            None,
            None,
        ),
        ([ev("Stop", last_assistant_message="Done.")], "waiting", "finished", "Done."),
        ([ev("Stop", last_assistant_message="Merge them?")], "waiting", "question", "Merge them?"),
        (
            [ev("UserPromptSubmit", prompt="hi"), ev("SubagentStop")],
            "working",
            None,
            None,
        ),
        (
            [ev("PreToolUse", tool_name="AskUserQuestion", tool_input=ASK)],
            "waiting",
            "question",
            "Do you prefer tea or coffee?",
        ),
        (
            [
                ev("PreToolUse", tool_name="AskUserQuestion", tool_input=ASK),
                ev("PermissionRequest", tool_name="AskUserQuestion", tool_input=ASK),
            ],
            "waiting",
            "question",
            "Do you prefer tea or coffee?",
        ),
        (
            [ev("UserPromptSubmit", prompt="hi"), ev("SomethingNew")],
            "working",
            None,
            None,
        ),
    ],
)
def test_transitions(
    events: list[HookEvent], status: str, kind: str | None, reason: str | None
) -> None:
    state, _ = run(*events)
    assert (state.status, state.kind, state.reason) == (status, kind, reason)


def test_fresh_state_records_where_it_runs() -> None:
    state, _ = apply(
        None,
        ev("SessionStart", source="startup", cwd=Path("/r"), transcript_path=Path("/t.jsonl")),
        7,
        50.0,
    )
    assert state.session_id == SID
    assert (state.pane_id, state.cwd, state.transcript_path) == (7, Path("/r"), Path("/t.jsonl"))
    assert state.started_at == state.updated_at == 50.0


def test_ping_only_on_entering_waiting() -> None:
    stop = ev("Stop", last_assistant_message="Done.")
    _, pings = run(ev("SessionStart"), stop, stop)
    assert len(pings) == 1
    assert pings[0].kind == "finished" and pings[0].reason == "Done." and pings[0].pane_id == 7
    _, pings = run(stop, ev("UserPromptSubmit", prompt="again"), stop)
    assert len(pings) == 2


def test_ask_then_its_permission_request_pings_once() -> None:
    _, pings = run(
        ev("PreToolUse", tool_name="AskUserQuestion", tool_input=ASK),
        ev("PermissionRequest", tool_name="AskUserQuestion", tool_input=ASK),
    )
    assert len(pings) == 1 and pings[0].kind == "question"


def test_permission_prompt_after_request_never_pings_again() -> None:
    state, pings = run(
        ev("PreToolUse", tool_name="Bash", tool_input=BASH),
        ev("PermissionRequest", tool_name="Bash", tool_input=BASH),
    )
    assert len(pings) == 1
    state = mark_seen(state, 103.0)
    state, ping = apply(
        state, ev("Notification", notification_type="permission_prompt", message="x"), 7, 104.0
    )
    assert ping is None
    assert state.status == "working"


def test_permission_prompt_without_request_pings() -> None:
    _, pings = run(
        ev("PreToolUse", tool_name="Bash", tool_input=BASH),
        ev("Notification", notification_type="permission_prompt", message="x"),
    )
    assert len(pings) == 1 and pings[0].kind == "permission"


def test_next_tool_clears_permission_requested() -> None:
    state, pings = run(
        ev("PreToolUse", tool_name="Bash", tool_input=BASH),
        ev("PermissionRequest", tool_name="Bash", tool_input=BASH),
        ev("PostToolUse", tool_name="Bash", tool_input=BASH),
        ev("PreToolUse", tool_name="Bash", tool_input=BASH),
        ev("Notification", notification_type="permission_prompt", message="x"),
    )
    assert len(pings) == 2
    assert state.status == "waiting"


def test_user_prompt_resets_turn() -> None:
    state, _ = run(
        ev("PostToolUse", tool_name="Edit", tool_input={"file_path": "/r/a.py"}),
        ev("UserPromptSubmit", prompt="next thing"),
    )
    assert (state.turn_edits, state.turn_reads, state.turn_commands, state.turn_other) == (
        0,
        0,
        0,
        0,
    )
    assert state.turn_files == ()
    assert state.last_prompt == "next thing"
    assert state.last_prompt_at == 101.0


def test_post_tool_use_counts() -> None:
    events = [
        ev("PostToolUse", tool_name=name, tool_input={"file_path": path})
        for name, path in [
            ("Edit", "/r/a.py"),
            ("Write", "/r/b.py"),
            ("MultiEdit", "/r/a.py"),
            ("NotebookEdit", "/r/n.ipynb"),
        ]
    ]
    events += [
        ev("PostToolUse", tool_name=n, tool_input={})
        for n in ("Read", "Grep", "Glob", "LS", "WebFetch", "WebSearch")
    ]
    events += [
        ev("PostToolUse", tool_name="Bash", tool_input=BASH),
        ev("PostToolUseFailure", tool_name="Bash", tool_input=BASH),
        ev("PostToolUse", tool_name="TodoWrite", tool_input={}),
    ]
    state, _ = run(*events)
    assert (state.turn_edits, state.turn_reads, state.turn_commands, state.turn_other) == (
        4,
        6,
        2,
        1,
    )
    assert state.turn_files == ("/r/a.py", "/r/b.py", "/r/n.ipynb")


def test_turn_files_capped_at_50() -> None:
    state, _ = run(
        *[
            ev("PostToolUse", tool_name="Edit", tool_input={"file_path": f"/r/{i}.py"})
            for i in range(60)
        ]
    )
    assert len(state.turn_files) == 50
    assert state.turn_edits == 60


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("Should I keep them separate?", "question"),
        ("Done.", "finished"),
        ("Done?  \n", "question"),
        ("Want me to open a PR?**", "question"),
        ("", "finished"),
    ],
)
def test_stop_kind(text: str, kind: str) -> None:
    state, _ = run(ev("Stop", last_assistant_message=text))
    assert state.kind == kind
    assert state.last_message == text


def test_status_since_moves_only_on_change() -> None:
    state, _ = run(
        ev("UserPromptSubmit", prompt="a"), ev("PreToolUse", tool_name="Read", tool_input={})
    )
    assert state.status_since == 100.0
    assert state.updated_at == 101.0


def test_mark_seen() -> None:
    finished, _ = run(ev("Stop", last_assistant_message="Done."))
    seen = mark_seen(finished, 200.0)
    assert (seen.status, seen.kind, seen.seen_at, seen.status_since) == ("idle", None, 200.0, 200.0)

    perm, _ = run(
        ev("PreToolUse", tool_name="Bash", tool_input=BASH),
        ev("PermissionRequest", tool_name="Bash", tool_input=BASH),
    )
    seen = mark_seen(perm, 200.0)
    assert (seen.status, seen.kind) == ("working", None)
    assert seen.current_tool == "Bash: uv run pytest tests/core -x"

    working, _ = run(ev("UserPromptSubmit", prompt="a"))
    assert mark_seen(working, 200.0) == working


def test_session_end_sets_ended_at() -> None:
    state, _ = run(ev("SessionStart"), ev("SessionEnd", reason="clear"))
    assert state.ended_at == 101.0


def waiting_since(t: float, pane: int = 7) -> SessionState:
    return SessionState(SID, pane_id=pane, status="waiting", kind="finished", status_since=t)


def fc(pane: int | None, at: float) -> FocusChange:
    return FocusChange(pane, at)


def test_seen_by_focus() -> None:
    s = waiting_since(100.0)
    assert not seen_by_focus(s, [fc(5, 99), fc(7, 100.5), fc(None, 101.2)], 110.0)
    assert seen_by_focus(s, [fc(7, 100.5), fc(3, 102)], 110.0)
    assert seen_by_focus(s, [fc(7, 100.5)], 101.6)
    assert not seen_by_focus(s, [fc(7, 100.5)], 101.2)
    # focus that began before the wait counts only from status_since
    assert not seen_by_focus(s, [fc(7, 50), fc(3, 100.5)], 110.0)
    assert seen_by_focus(s, [fc(7, 50), fc(3, 101.5)], 110.0)
    assert not seen_by_focus(s, [], 110.0)
    working = SessionState(SID, pane_id=7, status="working", status_since=100.0)
    assert not seen_by_focus(working, [fc(7, 100.5)], 110.0)


@pytest.mark.parametrize(
    ("tool", "inp", "summary"),
    [
        ("Bash", {"command": "uv run pytest\nmore"}, "Bash: uv run pytest"),
        ("Edit", {"file_path": "/r/a.py"}, "Edit /r/a.py"),
        ("NotebookEdit", {"notebook_path": "/r/n.ipynb"}, "NotebookEdit /r/n.ipynb"),
        ("Grep", {"pattern": "foo"}, "Grep foo"),
        ("WebFetch", {"url": "https://example.com"}, "WebFetch https://example.com"),
        ("Task", {"description": "Explore the repo"}, "Task: Explore the repo"),
        ("AskUserQuestion", ASK, "Do you prefer tea or coffee?"),
        ("mcp__github__create_issue", {}, "github: create_issue"),
        ("Mystery", {}, "Mystery"),
        ("Bash", {"command": 3}, "Bash"),
    ],
)
def test_tool_summary(tool: str, inp: dict, summary: str) -> None:
    assert tool_summary(tool, inp) == summary


def test_tool_summary_is_cut_to_80() -> None:
    s = tool_summary("Bash", {"command": "x" * 200})
    assert len(s) == 80 and s.endswith("…")


def test_ends_with_question() -> None:
    assert ends_with_question("Merge them?")
    assert ends_with_question('Open a PR?"\n')
    assert not ends_with_question("Done.")


def test_fresh_session_counts_its_age_from_its_first_event() -> None:
    # live check: a new session showed "idle 20747d", counted from the epoch
    state, _ = apply(None, ev("SessionStart", source="startup"), 7, 500.0)
    assert state.status_since == 500.0
