from pathlib import Path

import pytest

from cardak.cli import main
from cardak.core.navigate import Navigator
from cardak.ui.app import DeckApp


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.startswith("cardak 0.1.0")


def test_tui_runs_deck_with_config_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: list[DeckApp] = []
    monkeypatch.setattr(DeckApp, "run", lambda self: seen.append(self))
    assert main(["--config", str(tmp_path / "p.toml"), "tui"]) == 0
    assert seen[0].source.config_file == tmp_path / "p.toml"  # type: ignore[attr-defined]


def test_default_command_is_tui(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: list[DeckApp] = []
    monkeypatch.setattr(DeckApp, "run", lambda self: seen.append(self))
    monkeypatch.setenv("CARDAK_CONFIG", str(tmp_path / "c.toml"))
    assert main([]) == 0
    assert seen[0].source.config_file == tmp_path / "c.toml"  # type: ignore[attr-defined]


def test_wezterm_install_prints_snippet(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["wezterm", "install", "--dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"installed {tmp_path / 'cardak.lua'}")
    assert "require('cardak').apply(config)" in out


def test_wezterm_install_refuses_foreign_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "cardak.lua").write_text("-- mine\n")
    assert main(["wezterm", "install", "--dir", str(tmp_path)]) == 1
    assert "--force" in capsys.readouterr().err


def test_tui_gets_a_live_navigator(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: list[DeckApp] = []
    monkeypatch.setattr(DeckApp, "run", lambda self: seen.append(self))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert main(["--config", str(tmp_path / "p.toml")]) == 0
    nav = seen[0].navigator
    assert isinstance(nav, Navigator)
    (tmp_path / "wezterm").mkdir()
    (tmp_path / "wezterm" / "cardak.lua").write_text("x")
    assert nav.lua_installed()
