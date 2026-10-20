import json
from pathlib import Path

import pytest

from cardak.collectors.claude import ClaudeSession, session_meta


def test_reads_session_file(tmp_path: Path) -> None:
    (tmp_path / "123.json").write_text(
        json.dumps(
            {
                "pid": 123,
                "sessionId": "abc",
                "cwd": "/home/user/repos/webshop",
                "name": "webshop-12",
                "status": "busy",
            }
        )
    )
    assert session_meta(123, tmp_path) == ClaudeSession(
        "abc", "webshop-12", Path("/home/user/repos/webshop")
    )


def test_missing_file_means_no_prompt_yet(tmp_path: Path) -> None:
    (tmp_path / "123.0f1e.key").write_text("x")
    assert session_meta(123, tmp_path) is None


@pytest.mark.parametrize("content", ["not json", "[]", '{"name": "x"}', '{"sessionId": 5}'])
def test_malformed_file(tmp_path: Path, content: str) -> None:
    (tmp_path / "123.json").write_text(content)
    assert session_meta(123, tmp_path) is None


def test_optional_fields(tmp_path: Path) -> None:
    (tmp_path / "7.json").write_text('{"sessionId": "s"}')
    assert session_meta(7, tmp_path) == ClaudeSession("s", None, None)


def test_default_dir_is_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    d = tmp_path / ".claude" / "sessions"
    d.mkdir(parents=True)
    (d / "9.json").write_text('{"sessionId": "s9"}')
    assert session_meta(9) == ClaudeSession("s9", None, None)
