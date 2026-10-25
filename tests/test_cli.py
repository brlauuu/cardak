import os
import subprocess
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path

import pytest
from hookfix import text

from cardak.cli import main
from cardak.core.config import load
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


GIB = 2**30


class FakeSystemctl:
    def __init__(self, fail_enable: bool = False, scopes: dict[str, str] | None = None) -> None:
        self.calls: list[list[str]] = []
        self.fail_enable = fail_enable
        self.scopes = scopes or {}

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        args = argv[2:]
        if args[:1] == ["enable"] and self.fail_enable:
            return subprocess.CompletedProcess(argv, 1, "", "Failed to enable unit\n")
        if args[:1] == ["list-units"]:
            out = "".join(f"{s} loaded active running WezTerm\n" for s in self.scopes)
            return subprocess.CompletedProcess(argv, 0, out, "")
        if args[:1] == ["show"]:
            return subprocess.CompletedProcess(argv, 0, self.scopes[args[-1]] + "\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")


@pytest.fixture
def resources_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from cardak.collectors.machine import MachineSample, MachineSampler
    from cardak.systemd import install as unit

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setattr(
        "cardak.claude.install.find_executable", lambda argv0, which=None: Path("/u/bin/cardak")
    )
    monkeypatch.setattr(
        MachineSampler, "sample", lambda self: MachineSample(32 * GIB, GIB, 0, 0, None, 16)
    )
    fake = FakeSystemctl(scopes={"app-org.wezfurlong.wezterm-a.scope": "stop"})
    monkeypatch.setattr(unit, "run_systemctl", fake)
    return tmp_path, fake


def test_install_writes_unit_defaults_and_advice(resources_env, capsys) -> None:
    tmp_path, fake = resources_env
    cfg = tmp_path / "p.toml"
    assert main(["--config", str(cfg), "resources", "install", "--yes"]) == 0
    unit_file = tmp_path / "cfg/systemd/user/cardak.service"
    assert "ExecStart=/u/bin/cardak daemon" in unit_file.read_text()
    assert ["systemctl", "--user", "enable", "cardak.service"] in fake.calls
    assert ["systemctl", "--user", "restart", "cardak.service"] in fake.calls
    assert load(cfg).resources.pane_ram_gb == 8.0
    out = capsys.readouterr().out
    assert "pane_ram_gb" in out and "OOMPolicy=continue" in out


def test_install_skips_advice_when_dropin_exists(resources_env, capsys) -> None:
    tmp_path, _ = resources_env
    d = tmp_path / "cfg/systemd/user/app-org.wezfurlong.wezterm-.scope.d"
    d.mkdir(parents=True)
    (d / "oom.conf").write_text("[Scope]\nOOMPolicy=continue\n")
    assert main(["--config", str(tmp_path / "p.toml"), "resources", "install", "--yes"]) == 0
    assert "OOMPolicy=continue" not in capsys.readouterr().out


def test_install_keeps_user_values(resources_env) -> None:
    tmp_path, _ = resources_env
    cfg = tmp_path / "p.toml"
    cfg.write_text("[resources]\npane_ram_gb = 4\n")
    main(["--config", str(cfg), "resources", "install", "--yes"])
    assert load(cfg).resources.pane_ram_gb == 4.0
    main(["--config", str(cfg), "resources", "install", "--yes", "--refresh-defaults"])
    assert load(cfg).resources.pane_ram_gb == 8.0


def test_install_without_cardak_on_path(resources_env, monkeypatch, capsys) -> None:
    tmp_path, fake = resources_env
    monkeypatch.setattr("cardak.claude.install.find_executable", lambda argv0, which=None: None)
    assert main(["--config", str(tmp_path / "p.toml"), "resources", "install", "--yes"]) == 1
    assert "PATH" in capsys.readouterr().err
    assert not (tmp_path / "cfg/systemd/user/cardak.service").exists() and fake.calls == []


def test_install_reports_systemctl_failure(resources_env, monkeypatch, capsys) -> None:
    from cardak.systemd import install as unit

    tmp_path, _ = resources_env
    fake = FakeSystemctl(fail_enable=True)
    monkeypatch.setattr(unit, "run_systemctl", fake)
    assert main(["--config", str(tmp_path / "p.toml"), "resources", "install", "--yes"]) == 1
    assert "Failed to enable unit" in capsys.readouterr().err
    # nothing left half-installed: disabled again, the unit removed, so the deck still hints
    assert ["systemctl", "--user", "disable", "--now", "cardak.service"] in fake.calls
    assert not (tmp_path / "cfg/systemd/user/cardak.service").exists()


def test_failed_reinstall_restores_the_previous_unit(resources_env, monkeypatch) -> None:
    from cardak.systemd import install as unit

    tmp_path, _ = resources_env
    unit_file = tmp_path / "cfg/systemd/user/cardak.service"
    main(["--config", str(tmp_path / "p.toml"), "resources", "install", "--yes"])
    before = unit_file.read_text()
    monkeypatch.setattr(
        "cardak.claude.install.find_executable", lambda argv0, which=None: Path("/new/cardak")
    )
    monkeypatch.setattr(unit, "run_systemctl", FakeSystemctl(fail_enable=True))
    assert main(["--config", str(tmp_path / "p.toml"), "resources", "install", "--yes"]) == 1
    assert unit_file.read_text() == before


def test_install_asks_and_no_writes_nothing(resources_env, monkeypatch, capsys) -> None:
    tmp_path, fake = resources_env
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    assert main(["--config", str(tmp_path / "p.toml"), "resources", "install"]) == 0
    assert "Nothing written" in capsys.readouterr().out
    assert not (tmp_path / "cfg/systemd/user/cardak.service").exists() and fake.calls == []


def test_install_refuses_a_foreign_unit(resources_env, capsys) -> None:
    tmp_path, _ = resources_env
    unit_file = tmp_path / "cfg/systemd/user/cardak.service"
    unit_file.parent.mkdir(parents=True)
    unit_file.write_text("[Unit]\nDescription=mine\n")
    assert main(["--config", str(tmp_path / "p.toml"), "resources", "install", "--yes"]) == 1
    assert "--force" in capsys.readouterr().err
    assert (
        main(["--config", str(tmp_path / "p.toml"), "resources", "install", "--yes", "--force"])
        == 0
    )


def test_uninstall_removes_our_unit(resources_env, capsys) -> None:
    tmp_path, fake = resources_env
    main(["--config", str(tmp_path / "p.toml"), "resources", "install", "--yes"])
    assert main(["resources", "uninstall"]) == 0
    assert not (tmp_path / "cfg/systemd/user/cardak.service").exists()
    assert ["systemctl", "--user", "disable", "--now", "cardak.service"] in fake.calls


def test_uninstall_leaves_a_foreign_unit(resources_env, capsys) -> None:
    tmp_path, _ = resources_env
    unit_file = tmp_path / "cfg/systemd/user/cardak.service"
    unit_file.parent.mkdir(parents=True)
    unit_file.write_text("[Unit]\nDescription=mine\n")
    assert main(["resources", "uninstall"]) == 0
    assert unit_file.exists() and "not written by cardak" in capsys.readouterr().out


def test_resources_hint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from cardak.cli import RESOURCES_HINT, _resources_hint

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert _resources_hint() == RESOURCES_HINT
    assert "cardak resources install" in RESOURCES_HINT
    (tmp_path / "systemd/user").mkdir(parents=True)
    (tmp_path / "systemd/user/cardak.service").write_text("# cardak: generated\n")
    assert _resources_hint() is None


def test_skills_install_asks_and_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    assert main(["skills", "install", "--dir", str(tmp_path)]) == 0
    assert (tmp_path / "checkpoint" / "SKILL.md").exists()
    assert (tmp_path / "where-were-we" / "SKILL.md").exists()
    out = capsys.readouterr().out
    assert f"checkpoint: not installed: {tmp_path / 'checkpoint' / 'SKILL.md'}" in out
    assert main(["skills", "install", "--dir", str(tmp_path)]) == 0
    assert f"cardak skills already installed: {tmp_path}" in capsys.readouterr().out


def test_skills_install_no_answer_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    prompts: list[str] = []
    monkeypatch.setattr("builtins.input", lambda prompt: prompts.append(prompt) or "")
    assert main(["skills", "install", "--dir", str(tmp_path)]) == 0
    assert prompts == [f"Write 2 file(s) under {tmp_path}? [y/N] "]
    assert not (tmp_path / "checkpoint").exists()
    assert "Nothing written." in capsys.readouterr().out


def test_skills_install_yes_does_not_ask(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("builtins.input", lambda prompt: pytest.fail("asked"))
    assert main(["skills", "install", "--yes", "--dir", str(tmp_path)]) == 0
    assert (tmp_path / "checkpoint" / "SKILL.md").exists()


def test_skills_install_foreign_exit_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    mine = tmp_path / "checkpoint" / "SKILL.md"
    mine.parent.mkdir()
    mine.write_text("mine")
    monkeypatch.setattr("builtins.input", lambda prompt: pytest.fail("asked"))
    assert main(["skills", "install", "--dir", str(tmp_path)]) == 1
    assert "not written by cardak" in capsys.readouterr().err
    assert main(["skills", "install", "--yes", "--force", "--dir", str(tmp_path)]) == 0
    assert mine.read_text() != "mine"


def test_skills_status_lines(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    main(["skills", "install", "--yes", "--dir", str(tmp_path)])
    capsys.readouterr()
    other = tmp_path / "where-were-we" / "SKILL.md"
    other.write_text("mine")
    assert main(["skills", "status", "--dir", str(tmp_path)]) == 0
    assert capsys.readouterr().out.splitlines() == [
        f"checkpoint: installed: {tmp_path / 'checkpoint' / 'SKILL.md'}",
        f"where-were-we: not cardak's: {other}",
    ]


def test_skills_uninstall(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["skills", "install", "--yes", "--dir", str(tmp_path)])
    monkeypatch.setattr("builtins.input", lambda prompt: "yes")
    assert main(["skills", "uninstall", "--dir", str(tmp_path)]) == 0
    assert not (tmp_path / "checkpoint").exists()
    assert main(["skills", "uninstall", "--yes", "--dir", str(tmp_path)]) == 0
    assert "cardak skills already absent" in capsys.readouterr().out


def test_skills_hint_none_when_installed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from cardak.cli import _skills_hint

    monkeypatch.setenv("HOME", str(tmp_path))
    assert _skills_hint() is not None
    main(["skills", "install", "--yes"])
    assert (tmp_path / ".claude" / "skills" / "checkpoint" / "SKILL.md").exists()
    assert _skills_hint() is None


def test_install_dir_is_remembered_and_used(
    xdg: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from cardak.cli import _lua_dir

    custom = xdg / "dotfiles" / "wezterm"
    assert main(["wezterm", "install", "--dir", str(custom)]) == 0
    assert _lua_dir() == custom.resolve()
    cfg = xdg / "config/cardak/projects.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('[[project]]\nname = "a"\npath = "/a"\ntheme = "Nord"\n')
    capsys.readouterr()
    assert main(["themes", "apply"]) == 0
    out = capsys.readouterr().out
    assert (custom / "cardak_projects.lua").exists()  # next to the module it belongs to
    assert "run `cardak wezterm install`" not in out
    seen: list[DeckApp] = []
    monkeypatch.setattr(DeckApp, "run", lambda self: seen.append(self))
    monkeypatch.setattr("cardak.wezterm.schemes.read_cached_schemes", lambda cache_dir: None)
    assert main(["--config", str(cfg)]) == 0
    nav = seen[0].navigator
    assert isinstance(nav, Navigator) and nav.lua_installed()  # no install hint on jumps
    assert seen[0].theme_status is not None and seen[0].theme_status() is None


def test_removed_custom_dir_falls_back_to_default(xdg: Path) -> None:
    from cardak.cli import _lua_dir
    from cardak.wezterm.install import default_dir

    custom = xdg / "gone"
    main(["wezterm", "install", "--dir", str(custom)])
    (custom / "cardak.lua").unlink()
    assert _lua_dir() == default_dir()
