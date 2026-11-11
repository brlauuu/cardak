import os
import subprocess
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path

import pytest
from hookfix import text

from cardak.cli import main
from cardak.core.config import ProviderSettings, load
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


def test_deck_gets_the_summaries_screen(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from cardak.core.summary import ProviderTools

    seen: list[DeckApp] = []
    monkeypatch.setattr(DeckApp, "run", lambda self: seen.append(self))
    assert main(["--config", str(tmp_path / "p.toml"), "tui"]) == 0
    assert seen[0].summaries is seen[0].source
    tools = seen[0].providers
    assert isinstance(tools, ProviderTools)
    assert tools.read_key("ollama") is None and tools.read_key("openai") is None  # no key file
    assert "claude-haiku-5-5" in tools.prices()


def test_default_command_is_tui(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: list[DeckApp] = []
    monkeypatch.setattr(DeckApp, "run", lambda self: seen.append(self))
    monkeypatch.setenv("CARDAK_CONFIG", str(tmp_path / "c.toml"))
    assert main([]) == 0
    assert seen[0].source.config_file == tmp_path / "c.toml"  # type: ignore[attr-defined]


def test_deck_starts_the_spend_counter(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import threading

    from cardak.core import spend
    from cardak.core.spend import MonthSpend

    month = MonthSpend(0.0, 3.0, {}, ())  # handed through as is: the source judges its month
    stop = threading.Event()
    calls: list[object] = []
    monkeypatch.setattr(spend, "background", lambda log: calls.append(log) or (lambda: month, stop))
    seen: list[DeckApp] = []
    monkeypatch.setattr(DeckApp, "run", lambda self: seen.append(self))
    assert main(["--config", str(tmp_path / "p.toml"), "tui"]) == 0
    assert len(calls) == 1 and stop.is_set()
    assert seen[0].source._spend() is month  # type: ignore[attr-defined]


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


def _one_project(xdg: Path) -> Path:
    from cardak.wezterm.install import install

    install(xdg / "config/wezterm")
    project = xdg / "repos" / "webshop"
    project.mkdir(parents=True)
    cfg = xdg / "config/cardak/projects.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(f'[[project]]\nname = "webshop"\npath = "{project}"\n')
    return xdg / "config/cardak/logos/webshop.txt"


def test_themes_apply_makes_art_and_lists_the_image(xdg: Path) -> None:
    pytest.importorskip("PIL")  # the background extra (#131)
    art = _one_project(xdg)
    assert main(["themes", "apply"]) == 0
    assert "\\" in art.read_text()  # figlet letters
    lua = (xdg / "config/wezterm/cardak_projects.lua").read_text()
    assert "background = { file = [[" in lua and str(xdg / "cache/cardak/backgrounds") in lua


def test_themes_apply_without_the_background_extra_says_how_to_add_it(
    xdg: Path, capsys: pytest.CaptureFixture[str], block_extra: Callable[[str], None]
) -> None:
    from cardak.shared import extras

    block_extra("background")
    art = _one_project(xdg)
    assert main(["themes", "apply"]) == 0  # the scheme files are still written
    assert not art.exists()
    hint = extras.needs("background", "the project background")
    assert f"warning: {hint}" in capsys.readouterr().err


def test_themes_apply_twice_does_not_remake_deleted_art(xdg: Path) -> None:
    pytest.importorskip("PIL")  # the background extra (#131)
    art = _one_project(xdg)
    assert main(["themes", "apply"]) == 0
    art.unlink()
    assert main(["themes", "apply"]) == 0
    assert not art.exists()
    assert "background = {" not in (xdg / "config/wezterm/cardak_projects.lua").read_text()


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


def test_themes_zsh_draws_art_in_the_probed_scheme(
    xdg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("PIL")  # the background extra (#131)
    from PIL import Image as PilImage

    from cardak.core.themes import Scheme

    nord = Scheme("Nord", "#2e3440", "#d8dee9", None, ())
    monkeypatch.setattr("cardak.wezterm.schemes.read_cached_schemes", lambda cache_dir: None)
    monkeypatch.setattr("cardak.wezterm.schemes.load_schemes", lambda cache_dir: {"Nord": nord})
    project = xdg / "repos" / "webshop"
    project.mkdir(parents=True)
    cfg = xdg / "config/cardak/projects.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(f'[[project]]\nname = "webshop"\npath = "{project}"\ntheme = "Nord"\n')
    assert main(["themes", "zsh"]) == 0
    (png,) = (xdg / "cache/cardak/backgrounds").glob("*.png")
    img = PilImage.open(png).convert("RGBA")
    colors = {(r, g, b) for r, g, b, a in img.get_flattened_data() if a == 255}  # type: ignore[misc]
    assert colors == {(0xD8, 0xDE, 0xE9)}  # the scheme's foreground, not gray (#127)


def test_themes_zsh_warns_when_schemes_are_unknown(
    xdg: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from cardak.wezterm.schemes import SchemesUnavailable

    def fail(cache_dir: Path) -> dict[str, object]:
        raise SchemesUnavailable("wezterm not found")

    monkeypatch.setattr("cardak.wezterm.schemes.load_schemes", fail)
    assert main(["themes", "zsh"]) == 0
    assert "scheme names not checked: wezterm not found" in capsys.readouterr().err


def test_theme_writer_reports_old_module(tmp_path: Path) -> None:
    from cardak.cli import _theme_writer
    from cardak.core.backgrounds import Paths
    from cardak.core.config import Config
    from cardak.core.themes import SchemeStore
    from cardak.wezterm.install import MARKER

    (tmp_path / "wez").mkdir()
    (tmp_path / "wez" / "cardak.lua").write_text(MARKER + "\n")
    paths = Paths(tmp_path / "logos", tmp_path / "bg.json", tmp_path / "cache")
    write = _theme_writer(lambda: tmp_path / "wez", tmp_path / "card", SchemeStore(), paths)
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
    "PIL",
    "pyfiglet",
    "resvg_py",
    "cardak.core.backgrounds",
    "cardak.core.ascii_art",
    "cardak.core.alerts",
    "cardak.core.themes",
    "cardak.wezterm.generate",
    "cardak.wezterm.schemes",
    "anthropic",
    "cardak.llm",
    "cardak.core.summary",
    "cardak.core.summarize",
    "cardak.collectors.usage",
    "cardak.collectors.prices",
    "cardak.core.spend",
    "cardak.core.trends",
    "cardak.core.prices",
    "urllib.request",
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


STATUSLINE_FORBIDDEN = (*HOOK_FORBIDDEN, "textual", "sqlite3", "cardak.core.store")


def test_statusline_imports_only_what_it_needs(tmp_path: Path) -> None:
    plan = (Path(__file__).parent / "fixtures" / "statusline" / "plan.json").read_bytes()
    script = (
        "import io, sys\n"
        f"sys.stdin = io.TextIOWrapper(io.BytesIO({plan!r}))\n"
        "sys.argv = ['cardak', 'statusline', '--then', 'printf ok']\n"
        "import cardak.core.statusline as sl\n"
        "sl._become_sh = lambda command, data: print(command, end='') or 0\n"  # no exec over us
        "from cardak.cli import main\n"
        "assert main() == 0\n"
        f"loaded = [m for m in {STATUSLINE_FORBIDDEN!r} if m in sys.modules]\n"
        "assert not loaded, loaded\n"
        "print(' checked', end='')\n"
    )
    env = os.environ | {"XDG_STATE_HOME": str(tmp_path), "CARDAK_CONFIG": str(tmp_path / "p")}
    result = subprocess.run(
        [sys.executable, "-c", script], env=env, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "printf ok checked"  # the assertions ran: nothing exec'd over them
    assert (tmp_path / "cardak" / "limits.json").exists()


def test_summarize_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("anthropic")  # the anthropic extra (#131)
    from cardak.core import summarize
    from cardak.llm.anthropic import AnthropicSummarizer

    seen: list[tuple[list[str], Callable[..., object]]] = []
    monkeypatch.setattr(summarize, "main", lambda args, make: seen.append((list(args), make)) or 0)
    assert main(["summarize", "s", "1.5"]) == 0
    [(args, make)] = seen
    assert args == ["s", "1.5"]
    made = make("anthropic", ProviderSettings(), "sk-test")
    assert isinstance(made, AnthropicSummarizer) and made.model == "claude-haiku-5-5"


def test_make_summarizer_per_provider() -> None:
    pytest.importorskip("anthropic")  # the anthropic extra (#131)
    from cardak.cli import _make_summarizer
    from cardak.llm.anthropic import AnthropicSummarizer
    from cardak.llm.codex import CodexSummarizer
    from cardak.llm.ollama import OllamaSummarizer
    from cardak.llm.openai import OpenAISummarizer

    sonnet = _make_summarizer("anthropic", ProviderSettings(model="claude-sonnet-5-5"), "k")
    assert isinstance(sonnet, AnthropicSummarizer) and sonnet.model == "claude-sonnet-5-5"
    local = _make_summarizer("ollama", ProviderSettings("http://h:1", "m"), None)
    assert isinstance(local, OllamaSummarizer) and (local.url, local.model) == ("http://h:1", "m")
    fw = _make_summarizer("fireworks", ProviderSettings("https://f/v1", "fm"), "fk")
    assert isinstance(fw, OpenAISummarizer) and (fw.url, fw.key, fw.model) == (
        "https://f/v1",
        "fk",
        "fm",
    )
    oa = _make_summarizer("openai", ProviderSettings("http://o/v1", "om"), None)
    assert isinstance(oa, OpenAISummarizer) and oa.key is None
    cx = _make_summarizer("codex", ProviderSettings(), None)
    assert isinstance(cx, CodexSummarizer) and cx.model is None


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
    # ollama: the SDK is an extra, and this checks the ping path, not a provider (#131)
    config.write_text(
        '[notify]\ndesktop = false\nsound = "off"\n'
        '[summary]\nprovider = "ollama"\n[summary.ollama]\nmodel = "m"\n'
    )
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

    monkeypatch.setattr(daemon, "main", lambda web=None: 7)
    assert main(["daemon"]) == 7


@pytest.fixture
def web_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A config file with [web] on and a fresh key, both in scratch dirs; returns the config."""
    from cardak.core.config import web_key_path
    from cardak.web.key import ensure_key

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    cfg = tmp_path / "projects.toml"
    cfg.write_text('[web]\nenabled = true\nport = 8765\nhost = "127.0.0.1"\n')
    monkeypatch.setenv("CARDAK_CONFIG", str(cfg))
    ensure_key(web_key_path())
    return cfg


def daemon_web(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    from cardak.core import daemon

    seen: list[object] = []
    monkeypatch.setattr(daemon, "main", lambda web=None: seen.append(web) or 0)
    assert main(["daemon"]) == 0
    return seen


def test_cli_daemon_web_disabled_by_default(web_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    web_env.write_text("")
    assert daemon_web(monkeypatch) == [None]


def test_cli_daemon_web_off_when_key_is_open(
    web_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from cardak.core.config import web_key_path

    web_key_path().chmod(0o644)
    assert daemon_web(monkeypatch) == [None]
    assert "web view off" in capsys.readouterr().err


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_cli_daemon_web_waits_out_a_bad_config(
    web_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # #113: any error in projects.toml kept the web view off until the daemon restarted
    import urllib.request

    from test_web_view import snap

    web_env.write_text("[web]\nport = 0\n")
    (start,) = daemon_web(monkeypatch)
    assert start is not None  # agent status stays on: the web view may come up later
    publish = start(NoSession())  # type: ignore[operator]
    assert callable(publish)
    assert "web view waits for a readable" in capsys.readouterr().err
    publish(snap(), 100.0)  # the file hasn't changed: nothing tried, nothing logged
    assert capsys.readouterr().err == ""
    web_env.write_text("[web]\nport = 70000\n")  # still wrong
    publish(snap(), 102.0)
    assert "web view waits for a readable" in capsys.readouterr().err
    port = _free_port()
    web_env.write_text(f'[web]\nenabled = true\nport = {port}\nhost = "127.0.0.1"\n')
    publish(snap(), 104.0)
    assert f"web view on 127.0.0.1:{port}" in capsys.readouterr().err
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/snapshot")
    req.add_header("Authorization", f"Bearer {_key()}")
    with urllib.request.urlopen(req, timeout=5) as r:  # the snapshot that started it is served
        assert r.status == 200
    publish(snap(), 106.0)  # from now on it only publishes
    assert capsys.readouterr().err == ""


def test_cli_daemon_web_stops_waiting_when_the_fixed_config_has_it_off(
    web_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from test_web_view import snap

    web_env.write_text("[web]\nport = 0\n")
    (start,) = daemon_web(monkeypatch)
    publish = start(NoSession())  # type: ignore[operator]
    web_env.write_text("[web]\nenabled = false\n")
    publish(snap(), 100.0)
    web_env.write_text("[web]\nenabled = true\nport = 1\n")  # needs a restart, as before
    publish(snap(), 102.0)
    err = capsys.readouterr().err
    assert "web view on" not in err and "can't listen" not in err


def _key() -> str:
    from cardak.core.config import web_key_path

    return web_key_path().read_text().strip()


class NoSession:
    def session(self, session_id: str) -> None:
        return None


def test_cli_daemon_web_start_serves(web_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import socket
    import urllib.request

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    web_env.write_text(f'[web]\nenabled = true\nport = {port}\nhost = "127.0.0.1"\n')
    (start,) = daemon_web(monkeypatch)
    publish = start(NoSession())  # type: ignore[operator]
    assert callable(publish)
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as r:
        assert r.status == 200


def test_cli_daemon_web_start_bind_failure_returns_none(
    web_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import socket

    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]
        web_env.write_text(f'[web]\nenabled = true\nport = {port}\nhost = "127.0.0.1"\n')
        (start,) = daemon_web(monkeypatch)
        assert start(NoSession()) is None  # type: ignore[operator]
    assert "web view off" in capsys.readouterr().err


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


def test_install_says_it_writes_the_thresholds(resources_env, monkeypatch, capsys) -> None:
    # the prompt named only the unit; the [resources] thresholds were written too (#108)
    tmp_path, _ = resources_env
    cfg = tmp_path / "p.toml"
    prompts: list[str] = []
    monkeypatch.setattr("builtins.input", lambda prompt: prompts.append(prompt) or "n")
    assert main(["--config", str(cfg), "resources", "install"]) == 0
    out = capsys.readouterr().out
    assert "[resources]" in out and str(cfg) in out
    assert "thresholds" in prompts[0]
    assert not cfg.exists()


def _no_stdin(prompt: str) -> str:
    raise EOFError


def test_install_without_stdin_writes_nothing(resources_env, monkeypatch, capsys) -> None:
    tmp_path, fake = resources_env
    monkeypatch.setattr("builtins.input", _no_stdin)
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


def test_themes_zsh_prints_problems(xdg: Path, capsys: pytest.CaptureFixture[str]) -> None:
    wez = xdg / "config" / "wezterm"
    assert main(["wezterm", "install", "--dir", str(wez)]) == 0
    (wez / "cardak_projects.lua").write_text("-- mine\nreturn {}\n")  # not cardak's
    cfg = xdg / "config/cardak/projects.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text('[[project]]\nname = "a"\npath = "/a"\ntheme = "Nord"\n')
    capsys.readouterr()
    assert main(["themes", "zsh"]) == 0
    err = capsys.readouterr().err
    assert "warning: " in err and "cardak_projects.lua" in err  # was silently dropped (#27)


class WebSystemctl:
    def __init__(self, active: bool) -> None:
        self.active = active
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        if argv[2:3] == ["is-active"]:
            return subprocess.CompletedProcess(argv, 0 if self.active else 3, "", "")
        return subprocess.CompletedProcess(argv, 0, "", "")


@pytest.fixture
def web_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from cardak.systemd import install as unit
    from cardak.web import lan

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    cfg = tmp_path / "projects.toml"
    cfg.write_text('# mine\n[[project]]\nname = "webshop"\npath = "/home/user/repos/webshop"\n')
    monkeypatch.setenv("CARDAK_CONFIG", str(cfg))
    monkeypatch.setattr(lan, "lan_address", lambda: "192.168.1.20")
    fake = WebSystemctl(active=False)
    monkeypatch.setattr(unit, "run_systemctl", fake)
    return cfg, fake


def web_key() -> str:
    from cardak.core.config import web_key_path

    return web_key_path().read_text().strip()


def test_web_setup_creates_key_enables_and_prints_url(web_setup, capsys) -> None:
    from cardak.core.config import web_key_path

    cfg, _ = web_setup
    assert main(["web", "setup", "--yes"]) == 0
    out = capsys.readouterr().out
    assert f"http://192.168.1.20:8765/#k={web_key()}" in out
    assert web_key_path().stat().st_mode & 0o777 == 0o600
    assert load(cfg).web.enabled and "# mine" in cfg.read_text()
    assert "plain http" in out and "ufw" in out and "home screen" in out
    assert "cardak resources install" in out  # the service isn't running
    assert "█" in out or "▀" in out or "▄" in out  # the QR code


def test_web_setup_again_keeps_the_key(web_setup, capsys) -> None:
    main(["web", "setup", "--yes"])
    key = web_key()
    assert main(["web", "setup", "--yes"]) == 0
    assert web_key() == key
    assert "stops working" not in capsys.readouterr().out


def test_web_setup_new_key_warns_and_replaces(web_setup, capsys) -> None:
    main(["web", "setup", "--yes"])
    key = web_key()
    assert main(["web", "setup", "--yes", "--new-key"]) == 0
    assert web_key() != key
    assert "every device with the old link stops working" in capsys.readouterr().out


def test_web_setup_declined_writes_no_config(web_setup, monkeypatch, capsys) -> None:
    cfg, _ = web_setup
    before = cfg.read_text()
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    assert main(["web", "setup"]) == 1
    assert cfg.read_text() == before


def test_web_setup_without_stdin_writes_no_config(web_setup, monkeypatch, capsys) -> None:
    cfg, _ = web_setup
    before = cfg.read_text()
    monkeypatch.setattr("builtins.input", _no_stdin)
    assert main(["web", "setup"]) == 1
    assert cfg.read_text() == before
    assert "--yes" in capsys.readouterr().err


def test_web_setup_restarts_active_service_with_yes(web_setup) -> None:
    _, fake = web_setup
    fake.active = True
    assert main(["web", "setup", "--yes"]) == 0
    assert ["systemctl", "--user", "restart", "cardak.service"] in fake.calls


def test_web_setup_asks_before_restarting(web_setup, monkeypatch) -> None:
    _, fake = web_setup
    fake.active = True
    answers = iter(["y", "n"])  # turn it on: yes; restart: no
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    assert main(["web", "setup"]) == 0
    assert not any("restart" in c for c in fake.calls)


def test_web_off_disables(web_setup, capsys) -> None:
    cfg, _ = web_setup
    main(["web", "setup", "--yes"])
    assert main(["web", "off", "--yes"]) == 0
    assert not load(cfg).web.enabled
    assert web_key()  # the key stays


def test_web_status_not_serving(web_setup, capsys) -> None:
    import socket

    cfg, _ = web_setup
    with socket.socket() as s:  # a port nothing listens on, not the default a real daemon may hold
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    cfg.write_text(f"[web]\nport = {port}\n")
    assert main(["web", "status"]) == 0
    out = capsys.readouterr().out
    assert "off" in out and "not serving" in out and "No key yet" in out


def test_web_setup_brackets_an_ipv6_host(web_setup, capsys) -> None:
    cfg, _ = web_setup
    cfg.write_text('[web]\nhost = "::1"\n')
    assert main(["web", "setup", "--yes"]) == 0
    assert f"http://[::1]:8765/#k={web_key()}" in capsys.readouterr().out


def test_web_status_serving(web_setup, capsys) -> None:
    import socket

    from cardak.web.server import Latest, make_server, start

    cfg, _ = web_setup
    main(["web", "setup", "--yes"])
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    cfg.write_text(f"[web]\nenabled = true\nport = {port}\n")
    server = make_server("127.0.0.1", port, web_key(), Latest(), lambda sid: None)
    start(server, poll_interval=0.02)
    try:
        capsys.readouterr()
        assert main(["web", "status"]) == 0
        out = capsys.readouterr().out
    finally:
        server.shutdown()
        server.server_close()
    assert "on" in out and "serving" in out and "not serving" not in out
    assert f":{port}/#k={web_key()}" in out


def _block_pm_step(
    monkeypatch: pytest.MonkeyPatch, release_after: float | None
) -> tuple[list[bool], list[object], Callable[[], None]]:
    """Make the PM thread block in `step()` as if mid-query, and record each close of the PM
    view's store with whether its thread was still running then (#104)."""
    import threading

    from cardak.core import pm_service
    from cardak.core.store import Store

    in_step, release = threading.Event(), threading.Event()
    threads: list[object] = []
    pm_stores: list[Store] = []

    def step(self: pm_service.PmService) -> None:
        pm_stores.append(self._store)  # noqa: SLF001
        threads.append(threading.current_thread())
        in_step.set()
        release.wait(10)

    def run(self: DeckApp) -> None:
        assert in_step.wait(5)
        if release_after is not None:
            threading.Timer(release_after, release.set).start()

    closes: list[bool] = []
    real_close = Store.close

    def close(self: Store) -> None:
        if self in pm_stores:
            closes.append(any(t.is_alive() for t in threads))  # type: ignore[attr-defined]
        real_close(self)

    monkeypatch.setattr(pm_service.PmService, "step", step)
    monkeypatch.setattr(DeckApp, "run", run)
    monkeypatch.setattr(Store, "close", close)
    return closes, threads, release.set


def test_deck_closes_the_pm_store_after_its_thread(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    closes, threads, _ = _block_pm_step(monkeypatch, release_after=0.3)
    assert main(["--config", str(tmp_path / "p.toml"), "tui"]) == 0
    assert closes == [False]  # closed once, and only after the thread finished its step
    assert not any(t.is_alive() for t in threads)  # type: ignore[attr-defined]


def test_deck_leaves_the_pm_store_open_when_its_thread_hangs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from cardak.core import pm_service

    monkeypatch.setattr(pm_service, "STOP_WAIT", 0.1)
    closes, _, release = _block_pm_step(monkeypatch, release_after=None)
    try:
        assert main(["--config", str(tmp_path / "p.toml"), "tui"]) == 0
    finally:
        release()  # the thread finds its stop event set and ends
    assert closes == []  # still mid-step: closing it under the thread would crash (#104)


def test_deck_follows_a_reinstall_to_another_dir(
    xdg: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # #111: `cardak wezterm install --dir` while the deck runs moves where its files go
    from cardak.core.backgrounds import Paths
    from cardak.core.config import Config
    from cardak.core.themes import SchemeStore

    first, second = xdg / "first", xdg / "second"
    assert main(["wezterm", "install", "--dir", str(first)]) == 0
    seen: list[DeckApp] = []
    monkeypatch.setattr(DeckApp, "run", lambda self: seen.append(self))
    monkeypatch.setattr("cardak.wezterm.schemes.read_cached_schemes", lambda cache_dir: None)
    assert main([]) == 0
    assert main(["wezterm", "install", "--dir", str(second)]) == 0
    (first / "cardak.lua").unlink()
    nav = seen[0].navigator
    assert isinstance(nav, Navigator) and nav.lua_installed()
    assert seen[0].theme_status is not None and seen[0].theme_status() is None
    from cardak.cli import _lua_dir, _theme_writer

    paths = Paths(xdg / "logos", xdg / "bg.json", xdg / "cache")
    write = _theme_writer(_lua_dir, xdg / "card", SchemeStore(), paths)
    main(["wezterm", "install", "--dir", str(first)])
    write(Config())
    assert (first / "cardak_projects.lua").exists()
