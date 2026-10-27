import copy
import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from cardak.claude.install import (
    EVENTS,
    SettingsError,
    changes,
    default_settings_path,
    find_executable,
    hook_command,
    is_cardak,
    load_settings,
    save_settings,
    status,
    with_hooks,
    without_hooks,
)
from cardak.cli import HOOKS_HINT, _hooks_hint, main

FIXTURES = Path(__file__).parent / "fixtures" / "settings"
CMD = "/home/user/.local/bin/cardak hook"
NOW = datetime(2026, 10, 21, 12, 0, 0)


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def cardak_groups(settings: dict, event: str) -> list[dict]:
    return [
        g for g in settings.get("hooks", {}).get(event, [])
        if any(is_cardak(h.get("command", "")) for h in g.get("hooks", []))
    ]  # fmt: skip


def test_events() -> None:
    assert EVENTS == (
        "SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure",
        "PermissionRequest", "Notification", "Stop", "SessionEnd",
    )  # fmt: skip


def test_install_fresh() -> None:
    out = with_hooks(fixture("empty"), CMD)
    for event in EVENTS:
        assert out["hooks"][event] == [
            {"hooks": [{"type": "command", "command": CMD, "timeout": 5}]}
        ]
    assert status(out, CMD) == "installed"
    assert status(fixture("empty"), CMD) == "missing"


def test_install_is_idempotent_and_pure() -> None:
    before = fixture("user_hooks")
    snapshot = copy.deepcopy(before)
    once = with_hooks(before, CMD)
    assert with_hooks(once, CMD) == once
    assert before == snapshot


def test_install_keeps_user_hooks() -> None:
    user = fixture("user_hooks")
    out = with_hooks(user, CMD)
    assert out["model"] == user["model"] and out["permissions"] == user["permissions"]
    assert out["hooks"]["Stop"][0] == user["hooks"]["Stop"][0]
    assert out["hooks"]["PreToolUse"][0] == user["hooks"]["PreToolUse"][0]
    assert len(cardak_groups(out, "Stop")) == 1


def test_install_replaces_old_path() -> None:
    out = with_hooks(fixture("old_cardak"), CMD)
    groups = cardak_groups(out, "Stop")
    assert [g["hooks"][0]["command"] for g in groups] == [CMD]
    assert {"hooks": [{"type": "command", "command": "~/bin/notify-me stop"}]} in out["hooks"][
        "Stop"
    ]
    assert status(fixture("old_cardak"), CMD) == "partial"


def test_uninstall_round_trip() -> None:
    user = fixture("user_hooks")
    assert without_hooks(with_hooks(user, CMD)) == user
    assert without_hooks(with_hooks(fixture("empty"), CMD)) == {}
    assert without_hooks(fixture("old_cardak")) == {
        "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "~/bin/notify-me stop"}]}]}
    }


def test_status_partial() -> None:
    out = with_hooks(fixture("empty"), CMD)
    del out["hooks"]["Stop"]
    assert status(out, CMD) == "partial"


def test_changes() -> None:
    before = fixture("old_cardak")
    lines = changes(before, with_hooks(before, CMD))
    assert "+ SessionStart" in lines and "- Stop (/old/bin/cardak hook)" in lines
    assert "+ Stop" in lines
    assert changes(before, before) == []


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("/x/cardak hook", True),
        ("'/a b/cardak' hook", True),
        ("cardak hook", True),
        ("cardak-other hook", False),
        ("/x/cardak hooks", False),
        ("echo cardak hook", False),
        ("'unbalanced", False),
        ("", False),
    ],
)
def test_is_cardak(command: str, expected: bool) -> None:
    assert is_cardak(command) is expected


def test_hook_command_quotes() -> None:
    assert hook_command(Path("/a b/cardak")) == "'/a b/cardak' hook"
    assert is_cardak(hook_command(Path("/a b/cardak")))


def test_load_settings(tmp_path: Path) -> None:
    assert load_settings(tmp_path / "missing.json") == {}
    with pytest.raises(SettingsError):
        load_settings(FIXTURES / "invalid.json")
    (tmp_path / "list.json").write_text("[]")
    with pytest.raises(SettingsError):
        load_settings(tmp_path / "list.json")
    (tmp_path / "hooks.json").write_text('{"hooks": []}')
    with pytest.raises(SettingsError):
        with_hooks(load_settings(tmp_path / "hooks.json"), CMD)


def test_save_through_symlink_keeps_link_and_mode(tmp_path: Path) -> None:
    real = tmp_path / "dotfiles" / "settings.json"
    real.parent.mkdir()
    real.write_text('{"model": "opus"}\n')
    real.chmod(0o600)
    link = tmp_path / ".claude" / "settings.json"
    link.parent.mkdir()
    link.symlink_to(real)
    backup = save_settings(link, {"model": "sonnet"}, NOW)
    assert link.is_symlink()
    assert json.loads(real.read_text()) == {"model": "sonnet"}
    assert real.read_text().endswith("}\n")
    assert real.stat().st_mode & 0o777 == 0o600
    assert backup == link.parent / "settings.json.cardak-backup-20261021-120000"
    assert backup is not None and json.loads(backup.read_text()) == {"model": "opus"}


def test_save_new_file_has_no_backup(tmp_path: Path) -> None:
    path = tmp_path / ".claude" / "settings.json"
    assert save_settings(path, {}, NOW) is None
    assert path.read_text() == "{}\n"


def test_find_executable() -> None:
    assert find_executable("/home/u/.local/bin/cardak", lambda _n: None) == Path(
        "/home/u/.local/bin/cardak"
    )
    assert find_executable("/usr/bin/python3", lambda n: f"/opt/{n}") == Path("/opt/cardak")
    assert find_executable("python3", lambda _n: None) is None


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("cardak.cli.sys.argv", ["/home/user/.local/bin/cardak"])
    path = tmp_path / "settings.json"
    path.write_text((FIXTURES / "user_hooks.json").read_text())
    return path


def test_cli_install_asks(
    settings: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    before = settings.read_text()
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    assert main(["hooks", "install", "--settings", str(settings)]) == 0
    out = capsys.readouterr().out
    assert "+ SessionStart" in out and "backup" in out
    assert settings.read_text() == before
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")
    assert main(["hooks", "install", "--settings", str(settings)]) == 0
    assert status(json.loads(settings.read_text()), CMD) == "installed"


def _no_stdin(_prompt: str) -> str:
    raise EOFError


def test_cli_install_without_stdin_writes_nothing(
    settings: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    before = settings.read_text()
    monkeypatch.setattr("builtins.input", _no_stdin)
    assert main(["hooks", "install", "--settings", str(settings)]) == 0
    assert "Nothing written." in capsys.readouterr().out
    assert settings.read_text() == before


def test_cli_install_yes_then_already_installed(
    settings: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["hooks", "install", "--yes", "--settings", str(settings)]) == 0
    written = settings.read_text()
    backups = sorted(settings.parent.glob("settings.json.cardak-backup-*"))
    capsys.readouterr()
    assert main(["hooks", "install", "--yes", "--settings", str(settings)]) == 0
    assert "already installed" in capsys.readouterr().out
    assert settings.read_text() == written
    assert sorted(settings.parent.glob("settings.json.cardak-backup-*")) == backups


def test_cli_uninstall_and_status(settings: Path, capsys: pytest.CaptureFixture[str]) -> None:
    original = json.loads(settings.read_text())
    main(["hooks", "install", "--yes", "--settings", str(settings)])
    capsys.readouterr()
    assert main(["hooks", "status", "--settings", str(settings)]) == 0
    assert "installed" in capsys.readouterr().out
    assert main(["hooks", "uninstall", "--yes", "--settings", str(settings)]) == 0
    assert json.loads(settings.read_text()) == original
    capsys.readouterr()
    main(["hooks", "status", "--settings", str(settings)])
    assert "not installed" in capsys.readouterr().out


def test_invalid_json_aborts(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "settings.json"
    path.write_text((FIXTURES / "invalid.json").read_text())
    assert main(["hooks", "install", "--yes", "--settings", str(path)]) == 1
    assert "settings.json" in capsys.readouterr().err
    assert path.read_text() == (FIXTURES / "invalid.json").read_text()
    assert list(tmp_path.glob("*backup*")) == []


def test_cli_without_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("cardak.cli.sys.argv", ["python3"])
    monkeypatch.setattr("cardak.claude.install.shutil.which", lambda _n: None)
    assert main(["hooks", "install", "--yes", "--settings", str(tmp_path / "s.json")]) == 1
    assert "cardak" in capsys.readouterr().err
    assert not (tmp_path / "s.json").exists()


def test_default_settings_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    assert default_settings_path() == tmp_path / ".claude" / "settings.json"
    assert os.environ["HOME"] == str(tmp_path)


def test_hooks_hint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("cardak.cli.sys.argv", ["/home/user/.local/bin/cardak"])
    assert _hooks_hint() == HOOKS_HINT
    settings = tmp_path / ".claude" / "settings.json"
    save_settings(settings, with_hooks({}, CMD), NOW)
    assert _hooks_hint() is None
    settings.write_text("{broken")
    assert _hooks_hint() == HOOKS_HINT


def test_claude_written_settings_round_trip_bytes(tmp_path: Path) -> None:
    # Claude Code writes settings.json as JSON.stringify(…, null, 2): install + uninstall
    # gives back the same bytes
    original = (FIXTURES / "claude_written.json").read_text()
    path = tmp_path / "settings.json"
    path.write_text(original)
    save_settings(path, with_hooks(load_settings(path), "/x/cardak hook"), NOW)
    assert "/x/cardak hook" in path.read_text()
    save_settings(path, without_hooks(load_settings(path)), NOW)
    assert path.read_text() == original


@pytest.mark.parametrize("indent", ["    ", "\t"])
def test_four_space_and_tab_indent_kept(tmp_path: Path, indent: str) -> None:
    path = tmp_path / "settings.json"
    path.write_text(f'{{\n{indent}"model": "opus"\n}}\n')
    save_settings(path, with_hooks(load_settings(path), "/x/cardak hook"), NOW)
    lines = path.read_text().splitlines()
    assert lines[1] == f'{indent}"model": "opus",'
    assert lines[2].startswith(f'{indent}"hooks"') and lines[3].startswith(indent * 2)


def test_inline_arrays_are_expanded(tmp_path: Path) -> None:
    # what is not kept: hand formatting. The content is.
    path = tmp_path / "settings.json"
    path.write_text('{\n  "permissions": {"allow": ["Read"]}\n}\n')
    save_settings(path, load_settings(path), NOW)
    expanded = '{\n  "permissions": {\n    "allow": [\n      "Read"\n    ]\n  }\n}\n'
    assert path.read_text() == expanded
