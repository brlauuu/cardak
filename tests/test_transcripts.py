import json
from pathlib import Path

from cardak.collectors.transcripts import Entry, read_transcript

BASIC = Path(__file__).parent / "fixtures" / "transcripts" / "basic.jsonl"


def test_reads_fixture() -> None:
    entries, earlier = read_transcript(BASIC)
    assert earlier == 0
    assert [e.kind for e in entries] == ["user", "thinking", "tool", "assistant"]
    user, thinking, tool, assistant = entries
    assert user.text == "fix the symlink matching in the resolver"
    assert thinking.text == "Look at the resolver first."
    assert tool.tool_name == "Bash"
    assert tool.tool_input["command"] == "uv run pytest tests/test_resolver.py"
    assert tool.text == "3 passed in 0.12s"
    assert assistant.text == "The tests pass. Want me to open a PR?"


def test_limit() -> None:
    entries, earlier = read_transcript(BASIC, limit=2)
    assert [e.kind for e in entries] == ["tool", "assistant"]
    assert earlier == 2


def line(kind: str, content: object, **extra: object) -> str:
    return json.dumps({"type": kind, "message": {"role": kind, "content": content}, **extra})


def test_shapes_and_garbage(tmp_path: Path) -> None:
    f = tmp_path / "t.jsonl"
    f.write_text(
        "\n".join(
            [
                "not json",
                "[]",
                line("user", [{"type": "text", "text": "list prompt"}]),
                line("user", "side", isSidechain=True),
                line(
                    "assistant",
                    [
                        {
                            "type": "tool_use",
                            "id": "t1",
                            "name": "Read",
                            "input": {"file_path": "/a"},
                        }
                    ],
                ),  # fmt: skip
                line(
                    "user",
                    [
                        {
                            "type": "tool_result",
                            "tool_use_id": "t1",
                            "content": [{"type": "text", "text": "line 1"}],
                        }
                    ],
                ),  # fmt: skip
                line("user", [{"type": "tool_result", "tool_use_id": "unknown", "content": "x"}]),
                line("assistant", "plain string reply"),
                json.dumps({"type": "assistant", "message": "odd"}),
            ]
        )
    )
    entries, _ = read_transcript(f)
    assert entries == [
        Entry("user", "list prompt"),
        Entry("tool", "line 1", "Read", {"file_path": "/a"}),
        Entry("assistant", "plain string reply"),
    ]


def test_missing_file() -> None:
    assert read_transcript(Path("/nonexistent/t.jsonl")) == ([], 0)
