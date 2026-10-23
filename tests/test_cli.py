import os
import subprocess
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path

import pytest
from hookfix import text

from cardak.cli import main
from cardak.core.navigate import Navigator
from cardak.ui.app import DeckApp


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    # pyproject.toml is the single source of the version; the CLI must report it.
    pyproject = Path(__file__).parents[1] / "pyproject.toml"
    expected = tomllib.loads(pyproject.read_text())["project"]["version"]
    assert capsys.readouterr().out == f"cardak {expected}\n"


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
    monkeypatch.setattr("cardak.wezterm.schemes.read_cached_schemes", lambda cache_dir: None)
    assert main(["--config", str(tmp_path / "p.toml")]) == 0
    nav = seen[0].navigator
    assert isinstance(nav, Navigator)
    (tmp_path / "wezterm").mkdir()
    (tmp_path / "wezterm" / "cardak.lua").write_text("x")
    assert nav.lua_installed()


def test_wezterm_install_resolves_a_relative_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert main(["wezterm", "install", "--dir", "."]) == 0
    assert f"[==[{tmp_path}/?.lua;]==]" in capsys.readouterr().out


@pytest.fixture
def xdg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.delenv("CARDAK_CONFIG", raising=False)
    monkeypatch.setattr(
        "cardak.wezterm.schemes.load_schemes", lambda cache_dir: {}
    )  # no real probe
    return tmp_path


def test_themes_apply_without_module(xdg: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = xdg / "config/cardak/projects.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('[[project]]\nname = "a"\npath = "/a"\ntheme = "Nord"\n')
    assert main(["themes", "apply"]) == 0
    out = capsys.readouterr().out
    assert f"wrote {xdg / 'config/cardak/tints'}" in out
    assert "run `cardak wezterm install`" in out


def test_themes_apply_with_module(xdg: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from cardak.wezterm.install import install

    install(xdg / "config/wezterm")
    cfg = xdg / "config/cardak/projects.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('[[project]]\nname = "a"\npath = "/a"\ntheme = "Nord"\n')
    assert main(["themes", "apply"]) == 0
    assert (xdg / "config/wezterm/cardak_projects.lua").read_text().count("[[Nord]]") == 1
    assert main(["themes", "apply"]) == 0
    assert "already up to date" in capsys.readouterr().out


def test_themes_apply_broken_config(xdg: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = xdg / "config/cardak/projects.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("x = = y\n")
    assert main(["themes", "apply"]) == 1
    assert "projects.toml" in capsys.readouterr().err


def test_themes_zsh(xdg: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["themes", "zsh"]) == 0
    hook = xdg / "config/cardak/tint.zsh"
    assert hook.read_text().startswith("# cardak: generated")
    assert f"source {hook}" in capsys.readouterr().out
    hook.write_text("# mine\n")
    assert main(["themes", "zsh"]) == 1
    assert main(["themes", "zsh", "--force"]) == 0


def test_theme_writer_reports_old_module(tmp_path: Path) -> None:
    from cardak.cli import _theme_writer
    from cardak.core.config import Config
    from cardak.core.themes import SchemeStore
    from cardak.wezterm.install import MARKER

    (tmp_path / "wez").mkdir()
    (tmp_path / "wez" / "cardak.lua").write_text(MARKER + "\n")
    write = _theme_writer(tmp_path / "wez", tmp_path / "card", SchemeStore())
    assert any("older cardak" in w for w in write(Config()))


def test_hook_does_not_import_textual(tmp_path: Path) -> None:
    script = (
        "import io, sys\n"
        f"sys.stdin = io.StringIO({text('stop-finished')!r})\n"
        "from cardak.cli import main\n"
        "assert main(['hook']) == 0\n"
        "heavy = sorted(m for m in ('textual', 'psutil', 'rich') if m in sys.modules)\n"
        "assert not heavy, heavy\n"
    )
    config = tmp_path / "p.toml"
    config.write_text('[notify]\ndesktop = false\nsound = "off"\n')
    env = os.environ | {"XDG_STATE_HOME": str(tmp_path), "CARDAK_CONFIG": str(config)}
    result = subprocess.run(
        [sys.executable, "-c", script], env=env, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert (tmp_path / "cardak" / "cardak.db").exists()


HOOK_FORBIDDEN = (
    "argparse",
    "cardak.core.alerts",
    "cardak.core.themes",
    "cardak.wezterm.generate",
    "cardak.wezterm.schemes",
    "anthropic",
    "cardak.llm",
    "cardak.core.summary",
    "cardak.core.summarize",
)


def test_hook_imports_only_what_it_needs(tmp_path: Path) -> None:
    # review: 160-240 ms under load; the hook loaded the WezTerm modules, argparse and alerts
    script = (
        "import io, sys\n"
        f"sys.stdin = io.StringIO({text('user_prompt_submit')!r})\n"
        "sys.argv = ['cardak', 'hook']\n"
        "from cardak.cli import main\n"
        "assert main() == 0\n"
        f"loaded = [m for m in {HOOK_FORBIDDEN!r} if m in sys.modules]\n"
        "assert not loaded, loaded\n"
    )
    env = os.environ | {"XDG_STATE_HOME": str(tmp_path), "CARDAK_CONFIG": str(tmp_path / "p")}
    result = subprocess.run(
        [sys.executable, "-c", script], env=env, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_summarize_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    from cardak.core import summarize
    from cardak.llm.anthropic import AnthropicSummarizer

    seen: list[tuple[list[str], Callable[[str], object]]] = []
    monkeypatch.setattr(summarize, "main", lambda args, make: seen.append((list(args), make)) or 0)
    assert main(["summarize", "s", "1.5"]) == 0
    [(args, make)] = seen
    assert args == ["s", "1.5"]
    made = make("sk-test")
    assert isinstance(made, AnthropicSummarizer) and made.model == "claude-haiku-5-5"


def test_pinging_hook_imports_no_summarizer(tmp_path: Path) -> None:
    # the ping path loads alerts (it plays the sound); everything else stays out
    forbidden = tuple(m for m in HOOK_FORBIDDEN if m != "cardak.core.alerts")
    script = (
        "import io, sys\n"
        f"sys.stdin = io.StringIO({text('stop-finished')!r})\n"
        "sys.argv = ['cardak', 'hook']\n"
        "import cardak.core.alerts as alerts\n"
        "spawned = []\n"
        "alerts.spawn_detached = spawned.append\n"
        "from cardak.cli import main\n"
        "assert main() == 0\n"
        f"loaded = [m for m in {forbidden!r} if m in sys.modules]\n"
        "assert not loaded, loaded\n"
        "assert any('summarize' in argv for argv in spawned), spawned\n"
    )
    config = tmp_path / "p.toml"
    config.write_text('[notify]\ndesktop = false\nsound = "off"\n')
    key = tmp_path / "config" / "cardak" / "anthropic_key"
    key.parent.mkdir(parents=True)
    key.write_text("k")
    env = os.environ | {
        "XDG_STATE_HOME": str(tmp_path),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "CARDAK_CONFIG": str(config),
    }
    result = subprocess.run(
        [sys.executable, "-c", script], env=env, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_daemon_does_not_import_textual() -> None:
    script = (
        "import sys\n"
        "import cardak.core.daemon\n"
        "import cardak.core.source\n"
        "loaded = [m for m in ('textual', 'cardak.ui') if m in sys.modules]\n"
        "assert not loaded, loaded\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_daemon_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    from cardak.core import daemon

    monkeypatch.setattr(daemon, "main", lambda: 7)
    assert main(["daemon"]) == 7
