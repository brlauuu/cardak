import json
from pathlib import Path

import pytest
from hookfix import HOOKS, payload, text

from cardak.collectors.hooks import HookEvent, parse


def test_parse_pre_tool_use_bash() -> None:
    ev = parse(text("pre_tool_use-bash"))
    assert ev is not None
    assert ev.name == "PreToolUse"
    assert ev.tool_name == "Bash"
    assert ev.tool_input["command"] == "touch made.txt"
    assert ev.session_id == payload("pre_tool_use-bash")["session_id"]
    assert ev.cwd == Path("/home/user/repos/demo")
    assert ev.transcript_path is not None and ev.transcript_path.suffix == ".jsonl"


def test_parse_stop_has_last_message() -> None:
    ev = parse(text("stop-question"))
    assert ev is not None
    assert ev.last_assistant_message == "Should I merge the drafts or keep them separate?"


def test_parse_notification_type() -> None:
    ev = parse(text("notification-permission_prompt"))
    assert ev is not None
    assert ev.notification_type == "permission_prompt"
    assert ev.message == "Claude needs your permission"


def test_parse_session_events() -> None:
    start = parse(text("session_start-clear"))
    end = parse(text("session_end-clear"))
    prompt = parse(text("user_prompt_submit"))
    assert start is not None and start.source == "clear"
    assert end is not None and end.reason == "clear"
    assert prompt is not None and prompt.prompt is not None and "touch made.txt" in prompt.prompt


def test_parse_post_tool_use_failure() -> None:
    raw = payload("post_tool_use-bash") | {"hook_event_name": "PostToolUseFailure", "error": "boom"}
    raw.pop("tool_response")
    ev = parse(json.dumps(raw))
    assert ev is not None and ev.name == "PostToolUseFailure" and ev.tool_name == "Bash"


@pytest.mark.parametrize("name", sorted(p.stem for p in HOOKS.glob("*.json")))
def test_every_fixture_parses(name: str) -> None:
    ev = parse(text(name))
    assert isinstance(ev, HookEvent)
    assert ev.name == payload(name)["hook_event_name"]


@pytest.mark.parametrize(
    "raw",
    ["", "not json", "[]", '{"hook_event_name": "Stop"}', '{"session_id": 3}', "null"],
)
def test_parse_rejects(raw: str) -> None:
    assert parse(raw) is None


def test_parse_tolerates_wrong_types() -> None:
    ev = parse(text("pre_tool_use-bash", tool_input="ls", cwd=3, tool_name=["x"]))
    assert ev is not None
    assert ev.tool_input == {}
    assert ev.cwd is None
    assert ev.tool_name is None
