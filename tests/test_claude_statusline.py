import json
from pathlib import Path

import pytest

from cardak.claude.install import SettingsError
from cardak.claude.statusline import (
    changes,
    statusline_status,
    unwrap,
    with_statusline,
    without_statusline,
    wrapped_command,
)
from cardak.cli import main

EXE = Path("/u/bin/cardak")
MINE = {"type": "command", "command": "npx -y somestatus@latest", "padding": 0}


def test_wrapped_command() -> None:
    assert wrapped_command(EXE, None) == "/u/bin/cardak statusline"
    assert wrapped_command(EXE, "a b") == "/u/bin/cardak statusline --then 'a b'"
    assert wrapped_command(Path("/my dir/cardak"), None) == "'/my dir/cardak' statusline"


def test_unwrap() -> None:
    assert unwrap(wrapped_command(EXE, "a b")) == (True, "a b")
    assert unwrap(wrapped_command(EXE, None)) == (True, None)
    assert unwrap("npx -y somestatus@latest") == (False, None)
    assert unwrap("/u/bin/cardak hook") == (False, None)
    assert unwrap("/u/bin/cardak statusline --then") == (False, None)
    assert unwrap("'unclosed") == (False, None)


def test_wraps_existing_command_keeps_other_keys() -> None:
    after = with_statusline({"statusLine": MINE, "model": "x"}, EXE)
    assert after["model"] == "x"
    assert after["statusLine"]["padding"] == 0 and after["statusLine"]["type"] == "command"
    assert unwrap(after["statusLine"]["command"]) == (True, MINE["command"])
    assert statusline_status(after) == MINE["command"]


def test_wrap_round_trips_awkward_commands() -> None:
    awkward = """printf '%s' "$HOME" | tr a-z A-Z; echo 'x'"y" \\\\ `date`"""
    before = {"statusLine": {"type": "command", "command": awkward, "refreshInterval": 5}}
    assert without_statusline(with_statusline(before, EXE)) == before


def test_install_twice_is_unchanged() -> None:
    once = with_statusline({"statusLine": MINE}, EXE)
    assert with_statusline(once, EXE) == once
    assert with_statusline(once, Path("/new/cardak")) == once  # already cardak's: left alone


def test_no_statusline_added_then_removed() -> None:
    after = with_statusline({"model": "x"}, EXE)
    assert after["statusLine"] == {"type": "command", "command": "/u/bin/cardak statusline"}
    assert statusline_status(after) == ""
    assert without_statusline(after) == {"model": "x"}


def test_other_type_refused() -> None:
    with pytest.raises(SettingsError):
        with_statusline({"statusLine": {"type": "static", "text": "hi"}}, EXE)
    with pytest.raises(SettingsError):
        with_statusline({"statusLine": "npx x"}, EXE)


def test_uninstall_foreign_is_unchanged() -> None:
    before = {"statusLine": MINE}
    assert without_statusline(before) == before
    assert without_statusline({}) == {}
    assert statusline_status(before) is None


def test_changes() -> None:
    after = with_statusline({"statusLine": MINE}, EXE)
    assert changes({"statusLine": MINE}, after) == [
        f"- statusLine.command: {MINE['command']}",
        f"+ statusLine.command: {after['statusLine']['command']}",
    ]
    assert changes({}, with_statusline({}, EXE)) == [
        "+ statusLine.command: /u/bin/cardak statusline"
    ]


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("cardak.claude.install.find_executable", lambda argv0, which=None: EXE)
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"statusLine": MINE}, indent=2) + "\n")
    return path


def test_statusline_install_writes_backup(
    settings: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["statusline", "install", "--settings", str(settings), "--yes"]) == 0
    data = json.loads(settings.read_text())
    assert unwrap(data["statusLine"]["command"]) == (True, MINE["command"])
    assert list(settings.parent.glob("settings.json.cardak-backup-*"))
    assert "#97" in capsys.readouterr().out
    assert main(["statusline", "status", "--settings", str(settings)]) == 0
    assert f"installed, wrapping {MINE['command']}" in capsys.readouterr().out
    assert main(["statusline", "uninstall", "--settings", str(settings), "--yes"]) == 0
    assert json.loads(settings.read_text()) == {"statusLine": MINE}


def test_statusline_uninstall_nothing_to_do(
    settings: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = settings.read_text()
    assert main(["statusline", "uninstall", "--settings", str(settings), "--yes"]) == 0
    assert "not installed" in capsys.readouterr().out
    assert settings.read_text() == before
    assert not list(settings.parent.glob("*.cardak-backup-*"))


def _no_stdin(prompt: str) -> str:
    raise EOFError


def test_statusline_install_without_keyboard_writes_nothing(
    settings: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    before = settings.read_text()
    monkeypatch.setattr("builtins.input", _no_stdin)
    assert main(["statusline", "install", "--settings", str(settings)]) == 0
    assert "Nothing written" in capsys.readouterr().out
    assert settings.read_text() == before


def test_statusline_install_refuses_other_type(
    settings: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings.write_text(json.dumps({"statusLine": {"type": "static"}}))
    assert main(["statusline", "install", "--settings", str(settings), "--yes"]) == 1
    assert "statusLine" in capsys.readouterr().err


def test_statusline_help_is_not_the_wrapper(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["statusline", "--help"])
    assert "install" in capsys.readouterr().out
