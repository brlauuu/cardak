"""Command-line entry point: `cardak [--config PATH] [tui]`, `cardak wezterm install`, ….

`cardak hook` runs on every Claude Code hook (several per tool call), so it is dispatched
before argument parsing, and every other command imports what it needs when it runs.
`tests/test_cli.py::test_hook_imports_only_what_it_needs` guards this.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from cardak.core.config import (
    Config,
    ConfigError,
    WebSettings,
    anthropic_key_path,
    config_dir,
    config_path,
    load,
    state_dir,
)

if TYPE_CHECKING:
    import argparse

    from cardak.core.daemon import Publish, WebStart
    from cardak.core.source import LiveSource
    from cardak.core.themes import SchemeStore


STATUSLINE_SETUP = ("install", "uninstall", "status", "-h", "--help")


def build_parser() -> argparse.ArgumentParser:
    import argparse

    class _Version(argparse.Action):
        """--version, reading the package metadata only when asked (it is slow to import)."""

        def __call__(self, parser: argparse.ArgumentParser, *_args: object) -> None:
            from importlib.metadata import version

            print(f"cardak {version('cardak')}")
            parser.exit()

    parser = argparse.ArgumentParser(
        prog="cardak",
        description="Project-centric deck for WezTerm panes and Claude Code sessions.",
    )
    parser.add_argument("--version", action=_Version, nargs=0, help="show the version and exit")
    parser.add_argument(
        "--config",
        help="projects.toml to use (default: $CARDAK_CONFIG, else ~/.config/cardak/projects.toml)",
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("tui", help="open the deck (the default)")
    sub.add_parser("hook", help="record a Claude Code hook event (run by Claude Code)")
    sub.add_parser("daemon", help="watch resources and alert (run by the systemd service)")
    summ = sub.add_parser("summarize", help="summarize a waiting session (run by cardak hook)")
    summ.add_argument("session_id")
    summ.add_argument("wait_since")
    hooks = sub.add_parser("hooks", help="Claude Code hooks for agent status")
    hooks_sub = hooks.add_subparsers(dest="hooks_command", required=True)
    for name, text in (
        ("install", "add cardak's hooks to Claude Code's settings.json"),
        ("uninstall", "remove cardak's hooks from Claude Code's settings.json"),
        ("status", "show whether cardak's hooks are installed"),
    ):
        cmd = hooks_sub.add_parser(name, help=text)
        cmd.add_argument(
            "--settings", type=Path, help="settings file (default: ~/.claude/settings.json)"
        )
        if name != "status":
            cmd.add_argument("--yes", action="store_true", help="don't ask before writing")
    sl = sub.add_parser(
        "statusline", help="plan limits from Claude Code's status line (run by Claude Code)"
    )
    sl_sub = sl.add_subparsers(dest="statusline_command", required=True)
    for name, text in (
        ("install", "wrap your status line so the deck shows plan limits (a workaround, #97)"),
        ("uninstall", "put your own status line command back"),
        ("status", "show whether the status line is wrapped, and the last reading"),
    ):
        cmd = sl_sub.add_parser(name, help=text)
        cmd.add_argument(
            "--settings", type=Path, help="settings file (default: ~/.claude/settings.json)"
        )
        if name != "status":
            cmd.add_argument("--yes", action="store_true", help="don't ask before writing")
    sk = sub.add_parser("skills", help="the /checkpoint and /where-were-we Claude Code skills")
    sk_sub = sk.add_subparsers(dest="skills_command", required=True)
    for name, text in (
        ("install", "add /checkpoint and /where-were-we to ~/.claude/skills"),
        ("uninstall", "remove cardak's skills from ~/.claude/skills"),
        ("status", "show whether cardak's skills are installed"),
    ):
        cmd = sk_sub.add_parser(name, help=text)
        cmd.add_argument("--dir", type=Path, help="skills directory (default: ~/.claude/skills)")
        if name != "status":
            cmd.add_argument("--yes", action="store_true", help="don't ask before writing")
        if name == "install":
            cmd.add_argument(
                "--force", action="store_true", help="replace skill files not written by cardak"
            )
    res = sub.add_parser("resources", help="the background service that sends resource alerts")
    res_sub = res.add_subparsers(dest="resources_command", required=True)
    res_inst = res_sub.add_parser("install", help="install and start the alert service")
    res_inst.add_argument("--yes", action="store_true", help="don't ask before writing")
    res_inst.add_argument(
        "--refresh-defaults",
        action="store_true",
        help="recompute every [resources] threshold from this machine",
    )
    res_inst.add_argument(
        "--force", action="store_true", help="replace a cardak.service not written by cardak"
    )
    res_sub.add_parser("uninstall", help="stop and remove the alert service")
    web = sub.add_parser("web", help="the read-only page for your phone, on your Wi-Fi")
    web_sub = web.add_subparsers(dest="web_command", required=True)
    web_setup = web_sub.add_parser("setup", help="turn it on and show its link and QR code")
    web_setup.add_argument(
        "--new-key", action="store_true", help="replace the key (old links stop)"
    )
    web_setup.add_argument("--yes", action="store_true", help="don't ask before writing")
    web_off = web_sub.add_parser("off", help="turn it off")
    web_off.add_argument("--yes", action="store_true", help="don't ask before writing")
    web_sub.add_parser("status", help="whether it's on and served, and its link")
    wez = sub.add_parser("wezterm", help="WezTerm integration")
    wez_sub = wez.add_subparsers(dest="wezterm_command", required=True)
    inst = wez_sub.add_parser("install", help="install cardak.lua so jumps raise windows")
    inst.add_argument("--dir", type=Path, help="target directory (default: ~/.config/wezterm)")
    inst.add_argument(
        "--force", action="store_true", help="replace a cardak.lua not written by cardak"
    )
    themes = sub.add_parser("themes", help="per-project WezTerm color schemes")
    themes_sub = themes.add_subparsers(dest="themes_command", required=True)
    themes_sub.add_parser("apply", help="regenerate the theme files from projects.toml")
    zsh = themes_sub.add_parser("zsh", help="install the zsh hook that tints panes by project")
    zsh.add_argument(
        "--force", action="store_true", help="replace a tint.zsh not written by cardak"
    )
    return parser


HOOKS_HINT = "Run `cardak hooks install` to see Claude Code sessions' status."
RESOURCES_HINT = (
    "Resource alerts are off with the deck closed. Run `cardak resources install` to turn them on."
)


def _resources_hint() -> str | None:
    """The deck's one-time hint when the alert service isn't installed."""
    from cardak.systemd import install as unit

    return None if (unit.unit_dir() / unit.UNIT).exists() else RESOURCES_HINT


def _hooks_hint() -> str | None:
    """The deck's one-time hint when cardak's hooks aren't (fully) installed."""
    from cardak.claude import install as claude

    exe = claude.find_executable(sys.argv[0])
    try:
        settings = claude.load_settings(claude.default_settings_path())
    except claude.SettingsError:
        return HOOKS_HINT
    if exe is not None and claude.status(settings, claude.hook_command(exe)) == "installed":
        return None
    if exe is None and claude.status(settings, "cardak hook") != "missing":
        return None  # installed from some other path; nothing to compare it with
    return HOOKS_HINT


def _skills_hint() -> str | None:
    """The deck's hint when /checkpoint isn't installed."""
    from cardak.claude import skills

    return skills.hint(skills.default_dir())


SKILL_STATUS_TEXT = {
    "installed": "installed",
    "outdated": "outdated (run `cardak skills install`)",
    "missing": "not installed",
    "foreign": "not cardak's",
}


def _skills(command: str, dir_arg: Path | None, yes: bool, force: bool) -> int:
    from cardak.claude import skills

    dir = dir_arg or skills.default_dir()
    states = {name: skills.status(dir, name) for name in skills.NAMES}
    report = "\n".join(
        f"{n}: {SKILL_STATUS_TEXT[s]}: {dir / n / skills.FILE}" for n, s in states.items()
    )
    if command == "status":
        print(report)
        return 0
    install = command == "install"
    if install and not force and (foreign := _named(dir, states, ("foreign",))):
        print(f"cardak: not written by cardak (use --force): {foreign}", file=sys.stderr)
        return 1
    todo = _todo(states, install)
    if not todo:
        print(f"cardak skills already {'installed' if install else 'absent'}: {dir}")
        return 0
    print(report)
    verb = "Write" if install else "Remove"
    if not yes and not _ask(f"{verb} {todo} file(s) under {dir}? [y/N] "):
        print("Nothing written.")
        return 0
    return _skills_apply(dir, install, force)


def _todo(states: dict[str, str], install: bool) -> int:
    """Install writes all but the up-to-date ones; uninstall removes only cardak's own."""
    if install:
        return sum(s != "installed" for s in states.values())
    return sum(s in ("installed", "outdated") for s in states.values())


def _named(dir: Path, states: dict[str, str], wanted: tuple[str, ...]) -> str:
    """The skill files whose status is one of wanted, comma-separated."""
    return ", ".join(str(dir / n / "SKILL.md") for n, s in states.items() if s in wanted)


def _ask(prompt: str) -> bool:
    """Ask a yes/no question; no stdin (a `!` command, a pipe) answers no."""
    try:
        return input(prompt).strip().lower() in ("y", "yes")
    except EOFError:
        print("\ncardak: no answer on stdin; run it in a terminal, or pass --yes", file=sys.stderr)
        return False


def _skills_apply(dir: Path, install: bool, force: bool) -> int:
    from cardak.claude import skills

    try:
        done = skills.install(dir, force) if install else skills.uninstall(dir)
    except (skills.SkillsError, OSError) as e:
        print(f"cardak: {e}", file=sys.stderr)
        return 1
    print("\n".join(f"{'wrote' if install else 'removed'} {p}" for p in done))
    return 0


HOOK_STATUS_TEXT = {
    "installed": "installed",
    "partial": "partly installed (run `cardak hooks install`)",
    "missing": "not installed",
}


def _hooks(command: str, settings_arg: Path | None, yes: bool) -> int:
    from cardak.claude import install as claude

    path = settings_arg or claude.default_settings_path()
    exe = claude.find_executable(sys.argv[0])
    if exe is None:
        print(
            "cardak: can't find the cardak executable to register; is it on PATH?", file=sys.stderr
        )
        return 1
    hook = claude.hook_command(exe)
    try:
        before = claude.load_settings(path)
        if command == "status":
            print(f"{HOOK_STATUS_TEXT[claude.status(before, hook)]}: {path}")
            return 0
        install = command == "install"
        after = claude.with_hooks(before, hook) if install else claude.without_hooks(before)
    except claude.SettingsError as e:
        print(f"cardak: {e}", file=sys.stderr)
        return 1
    if after == before:
        print(f"cardak hooks already {'installed' if install else 'absent'}: {path}")
        return 0
    done = "Done. New Claude Code sessions report their status to the deck." if install else "Done."
    return _write_settings(path, after, yes, claude.changes(before, after), done)


def _write_settings(path: Path, after: dict, yes: bool, lines: list[str], done: str) -> int:
    """Show what changes, ask (unless --yes), back up and write settings.json."""
    from datetime import datetime

    from cardak.claude import install as claude

    print(f"{path}:")
    print("\n".join(f"  {line}" for line in lines))
    now = datetime.now()
    if path.exists():
        print(f"A backup goes to {claude.backup_path(path, now)}")
    if not yes and not _ask(f"Write {path}? [y/N] "):
        print("Nothing written.")
        return 0
    try:
        claude.save_settings(path, after, now)
    except OSError as e:
        print(f"cardak: cannot write {path}: {e}", file=sys.stderr)
        return 1
    print(done)
    return 0


STATUSLINE_DONE = (
    "Done. Claude Code sessions report plan limits to the deck after their next reply. This wraps "
    "your status line as a workaround until Claude Code offers another way (#97)."
)


def _statusline(command: str, settings_arg: Path | None, yes: bool) -> int:
    from cardak.claude import install as claude
    from cardak.claude import statusline as wrap

    path = settings_arg or claude.default_settings_path()
    exe = claude.find_executable(sys.argv[0])
    if exe is None:
        print("cardak: can't find the cardak executable; is it on PATH?", file=sys.stderr)
        return 1
    try:
        before = claude.load_settings(path)
        if command == "status":
            return _statusline_status(path, wrap.statusline_status(before))
        install = command == "install"
        after = wrap.with_statusline(before, exe) if install else wrap.without_statusline(before)
    except claude.SettingsError as e:
        print(f"cardak: {e}", file=sys.stderr)
        return 1
    if after == before:
        print(f"cardak's status line {'already installed' if install else 'not installed'}: {path}")
        return 0
    done = STATUSLINE_DONE if install else "Done. Your own status line is back."
    return _write_settings(path, after, yes, wrap.changes(before, after), done)


def _statusline_status(path: Path, wrapped: str | None) -> int:
    import time

    from cardak.core.limits import limits_path, load

    if wrapped is None:
        print(f"not installed: {path}")
        return 0
    print(f"installed, wrapping {wrapped}: {path}" if wrapped else f"installed: {path}")
    stored = load(limits_path())
    if stored is None:
        print("no plan limits read yet")
    else:
        print(f"last reading {round((time.time() - stored.read_at) / 60)} min ago")
    return 0


def _resources_install(config_arg: str | None, yes: bool, refresh: bool, force: bool) -> int:
    from cardak.claude.install import find_executable
    from cardak.systemd import install as unit

    exe = find_executable(sys.argv[0])
    if exe is None:
        print("cardak: can't find the cardak executable; is it on PATH?", file=sys.stderr)
        return 1
    path = unit.unit_dir() / unit.UNIT
    text = unit.unit_text(exe)
    print(f"{path}:")
    print("\n".join(f"  {line}" for line in text.splitlines()))
    if not yes and not _ask(f"Write {path} and start it? [y/N] "):
        print("Nothing written.")
        return 0
    previous = path.read_text(encoding="utf-8") if unit.is_ours(path) else None
    try:
        unit.write_unit(path, text, force)
    except OSError as e:
        print(f"cardak: {e}", file=sys.stderr)
        return 1
    if error := unit.enable(unit.run_systemctl):
        print(f"cardak: {error}", file=sys.stderr)
        _roll_back(path, previous)
        return 1
    print("The alert service is running; it keeps watching with the deck closed.")
    _resource_defaults(config_arg, refresh)
    if "stop" in unit.oom_policies(unit.run_systemctl).values() and not unit.dropin_present(
        unit.unit_dir()
    ):
        print()
        print(unit.oom_advice(unit.unit_dir()), end="")
    return 0


def _roll_back(path: Path, previous: str | None) -> None:
    """After a failed enable, leave nothing half-installed: enable links before restart runs."""
    from cardak.systemd import install as unit

    unit.disable(unit.run_systemctl)
    try:
        if previous is None:
            path.unlink(missing_ok=True)
        else:
            unit.write_unit(path, previous)
            unit.enable(unit.run_systemctl)
    except OSError as e:
        print(f"cardak: cannot restore {path}: {e}", file=sys.stderr)


def _resource_defaults(config_arg: str | None, refresh: bool) -> None:
    from cardak.collectors.machine import MachineSampler
    from cardak.core.config import write_resource_defaults
    from cardak.core.resources import default_entries

    sample = MachineSampler().sample()
    if sample is None:
        print(
            "cardak: can't read this machine's memory; [resources] left as it is", file=sys.stderr
        )
        return
    path = config_path(config_arg)
    try:
        written = write_resource_defaults(
            path, default_entries(sample.mem_total, sample.n_cpus), refresh
        )
    except ConfigError as e:
        print(f"cardak: thresholds not written: {e}", file=sys.stderr)
        return
    if written:
        print(f"Thresholds written to [resources] in {path}: {', '.join(written)}")


def _resources_uninstall() -> int:
    from cardak.systemd import install as unit

    path = unit.unit_dir() / unit.UNIT
    if path.exists() and not unit.is_ours(path):
        print(f"{path} was not written by cardak; left as it is.")
        return 0
    if error := unit.disable(unit.run_systemctl):
        print(f"cardak: {error}", file=sys.stderr)
        return 1
    path.unlink(missing_ok=True)
    print("The alert service is stopped and removed.")
    return 0


def _wezterm_install(dest: Path | None, force: bool) -> int:
    from cardak.wezterm.install import InstallError, default_dir, install, snippet

    # absolute: package.path is resolved against WezTerm's cwd, not this shell's
    dest_dir = dest.expanduser().resolve() if dest else default_dir()
    try:
        result = install(dest_dir, force=force)
    except InstallError as e:
        print(f"cardak: {e}", file=sys.stderr)
        return 1
    _remember_lua_dir(dest_dir)
    print(f"{result.status} {result.path}\n")
    print(snippet(dest_dir), end="")
    return 0


LUA_DIR_FILE = "wezterm_dir"


def _remember_lua_dir(path: Path) -> None:
    """Where `cardak wezterm install` put cardak.lua: the deck and `themes` follow it (#19)."""
    record = state_dir() / LUA_DIR_FILE
    try:
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text(f"{path}\n", encoding="utf-8")
    except OSError as e:
        print(f"cardak: couldn't note the install dir in {record}: {e}", file=sys.stderr)


def _lua_dir() -> Path:
    """cardak.lua's directory: the one it was last installed to, while it's there."""
    from cardak.wezterm.install import default_dir

    try:
        recorded = Path((state_dir() / LUA_DIR_FILE).read_text(encoding="utf-8").strip())
    except (OSError, UnicodeDecodeError):
        return default_dir()
    if recorded.is_absolute() and (recorded / "cardak.lua").exists():
        return recorded
    return default_dir()


def _module_message(version: int | None) -> str | None:
    from cardak.wezterm.generate import MODULE_VERSION, NO_MODULE_MSG, OLD_MODULE_MSG

    if version is None:
        return NO_MODULE_MSG
    if version < MODULE_VERSION:
        return OLD_MODULE_MSG
    return None


def _theme_writer(
    wezterm_dir: Path, cardak_dir: Path, store: SchemeStore
) -> Callable[[Config], list[str]]:
    """What the deck runs after every config change: regenerate, return banner warnings."""
    from cardak.wezterm.generate import MODULE_VERSION, OLD_MODULE_MSG, write_generated

    def write(config: Config) -> list[str]:
        result = write_generated(wezterm_dir, cardak_dir, config, store.get() or None)
        warnings = [
            f"projects.toml: {p}" if p.startswith("unknown") else p for p in result.problems
        ]
        if result.module_version is not None and result.module_version < MODULE_VERSION:
            warnings.append(OLD_MODULE_MSG)
        return warnings

    return write


def _themes_apply(config_arg: str | None) -> int:
    from cardak.wezterm.generate import write_generated
    from cardak.wezterm.schemes import SchemesUnavailable, default_cache_dir, load_schemes

    path = config_path(config_arg)
    try:
        config = load(path)
    except ConfigError as e:
        print(f"cardak: {e}", file=sys.stderr)
        return 1
    try:
        schemes = load_schemes(default_cache_dir())
    except SchemesUnavailable as e:
        print(f"cardak: scheme names not checked: {e}", file=sys.stderr)
        schemes = None
    result = write_generated(_lua_dir(), config_dir(), config, schemes)
    for written in result.written:
        print(f"wrote {written}")
    if not result.written:
        print("theme files already up to date")
    for problem in result.problems:
        print(f"warning: {problem}", file=sys.stderr)
    if message := _module_message(result.module_version):
        print(message)
    return 0


def _themes_zsh(config_arg: str | None, force: bool) -> int:
    from cardak.wezterm.generate import GenerateError, install_tint_hook, write_generated
    from cardak.wezterm.schemes import default_cache_dir, read_cached_schemes

    try:
        config = load(config_path(config_arg))
    except ConfigError as e:
        print(f"cardak: {e}", file=sys.stderr)
        return 1
    cardak_dir = config_dir()
    try:
        hook = install_tint_hook(cardak_dir, force=force)
    except GenerateError as e:
        print(f"cardak: {e}", file=sys.stderr)
        return 1
    schemes = read_cached_schemes(default_cache_dir())
    result = write_generated(_lua_dir(), cardak_dir, config, schemes)
    for problem in result.problems:
        print(f"warning: {problem}", file=sys.stderr)
    print(f"installed {hook}\n\nAdd this line to your ~/.zshrc:\n\nsource {hook}\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    given = sys.argv[1:] if argv is None else argv
    if given == ["hook"]:
        from cardak.core import hook

        return hook.main(sys.stdin)
    if given[:1] == ["statusline"] and given[1:2] not in [[c] for c in STATUSLINE_SETUP]:
        from cardak.core import statusline

        return statusline.main(given[1:], sys.stdin.buffer)
    args = build_parser().parse_args(argv)
    if args.command == "daemon":
        return _daemon()
    if args.command == "summarize":
        return _summarize(args.session_id, args.wait_since)
    if args.command == "hooks":
        return _hooks(args.hooks_command, args.settings, getattr(args, "yes", False))
    if args.command == "statusline":
        return _statusline(args.statusline_command, args.settings, getattr(args, "yes", False))
    if args.command in ("skills", "resources", "wezterm", "themes", "web"):
        return _setup(args)
    return _deck(args.config)


def _daemon() -> int:
    from cardak.core import daemon

    return daemon.main(web=_web_start())


def _web_start() -> WebStart | None:
    """What starts the web view inside the daemon, or None when it stays off (logged)."""
    from cardak.core.config import web_key_path
    from cardak.core.daemon import log
    from cardak.web.key import WebKeyError, read_key

    try:
        web = load(config_path(None)).web
    except ConfigError as e:
        log(f"web view off: {e}")
        return None
    if not web.enabled:
        return None
    try:
        key = read_key(web_key_path())
    except WebKeyError as e:
        log(f"web view off: {e}")
        return None

    def start_web(source: LiveSource) -> Publish | None:
        from cardak.web.server import Latest, make_server, start

        latest = Latest()
        try:
            server = make_server(web.host, web.port, key, latest, source.session)
        except OSError as e:
            log(f"web view off: can't listen on {web.host}:{web.port}: {e.strerror or e}")
            return None
        start(server)
        log(f"web view on {web.host}:{web.port}")
        return latest.publish

    return start_web


def _web(command: str, config_arg: str | None, new_key: bool, yes: bool) -> int:
    path = config_path(config_arg)
    try:
        web = load(path).web
    except ConfigError as e:
        print(f"cardak: {e}", file=sys.stderr)
        return 1
    if command == "status":
        return _web_status(web)
    if command == "off":
        return _web_off(path, web, yes)
    return _web_setup(path, web, new_key, yes)


def _web_url(web: WebSettings, key: str) -> str:
    from cardak.web import lan

    host = web.host
    if host in ("0.0.0.0", "::", ""):
        host = lan.lan_address() or "localhost"
    return f"http://{host}:{web.port}/#k={key}"


def _web_setup(path: Path, web: WebSettings, new_key: bool, yes: bool) -> int:
    from cardak.core.config import set_web_enabled, web_key_path
    from cardak.web.key import WebKeyError, ensure_key

    had_key = web_key_path().exists()
    try:
        key, created = ensure_key(web_key_path(), new=new_key)
    except (WebKeyError, OSError) as e:
        print(f"cardak: {e}", file=sys.stderr)
        return 1
    if created and had_key:
        print("New key: every device with the old link stops working.")
    changed = created
    if not web.enabled:
        if not yes and not _ask(f"Turn on the web view in {path}? [y/N] "):
            print(f"Nothing changed in {path}.")
            return 1
        try:
            set_web_enabled(path, True)
        except ConfigError as e:
            print(f"cardak: {e}", file=sys.stderr)
            return 1
        changed = True
    _web_link(web, key)
    _offer_restart(yes, changed)
    return 0


def _web_link(web: WebSettings, key: str) -> None:
    import segno

    from cardak.web import lan

    url = _web_url(web, key)
    print(f"\n{url}\n")
    segno.make(url, error="l").terminal(compact=True)
    if web.host in ("0.0.0.0", "::", "") and lan.lan_address() is None:
        print("No network address found; connect to your Wi-Fi and run `cardak web setup` again.")
    elif web.host.startswith("127."):
        print(f"[web] host is {web.host}: only this computer can open it.")
    print(
        "\nOpen it or scan the code on each device, then bookmark it or add it to the home screen "
        "from that page.\n"
        "It's plain http: anyone on this Wi-Fi who records traffic could read it. "
        "`cardak web setup --new-key` replaces the key.\n"
        "If a firewall such as ufw is on: "
        f"`sudo ufw allow from 192.168.0.0/16 to any port {web.port} proto tcp`."
    )


def _offer_restart(yes: bool, changed: bool) -> None:
    from cardak.systemd import install as unit

    if not unit.is_active(unit.run_systemctl):
        print("Run `cardak resources install` to start the background service; it serves the page.")
        return
    if not changed:
        return
    if not yes and not _ask("Restart cardak's background service so it takes effect? [y/N] "):
        print("Restart it later with: systemctl --user restart cardak")
        return
    if error := unit.restart(unit.run_systemctl):
        print(f"cardak: {error}", file=sys.stderr)
    else:
        print("Restarted the background service.")


def _web_off(path: Path, web: WebSettings, yes: bool) -> int:
    from cardak.core.config import set_web_enabled

    if not web.enabled:
        print("The web view is already off.")
        return 0
    if not yes and not _ask(f"Turn off the web view in {path}? [y/N] "):
        print(f"Nothing changed in {path}.")
        return 1
    try:
        set_web_enabled(path, False)
    except ConfigError as e:
        print(f"cardak: {e}", file=sys.stderr)
        return 1
    print("The web view is off; the key stays for when you turn it on again.")
    _offer_restart(yes, True)
    return 0


def _web_status(web: WebSettings) -> int:
    import urllib.request

    from cardak.core.config import web_key_path
    from cardak.web.key import WebKeyError, read_key

    print(f"Web view: {'on' if web.enabled else 'off'} ({web.host}:{web.port})")
    host = "127.0.0.1" if web.host in ("0.0.0.0", "::", "", "127.0.0.1") else web.host
    try:
        with urllib.request.urlopen(f"http://{host}:{web.port}/", timeout=1) as r:
            serving = r.status == 200
    except OSError:
        serving = False
    print("serving" if serving else "not serving")
    if not web_key_path().exists():
        print("No key yet: run `cardak web setup`.")
        return 0
    try:
        print(_web_url(web, read_key(web_key_path())))
    except WebKeyError as e:
        print(f"No usable key: {e}")
    print("`cardak web setup` shows the QR code again; it keeps the key.")
    return 0


def _setup(args: argparse.Namespace) -> int:
    """The commands that install or generate files: skills, resources, wezterm, themes, web."""
    if args.command == "web":
        new_key, yes = getattr(args, "new_key", False), getattr(args, "yes", False)
        return _web(args.web_command, args.config, new_key, yes)
    if args.command == "skills":
        yes, force = getattr(args, "yes", False), getattr(args, "force", False)
        return _skills(args.skills_command, args.dir, yes, force)
    if args.command == "resources":
        if args.resources_command == "uninstall":
            return _resources_uninstall()
        return _resources_install(args.config, args.yes, args.refresh_defaults, args.force)
    if args.command == "wezterm":
        return _wezterm_install(args.dir, args.force)
    if args.themes_command == "apply":
        return _themes_apply(args.config)
    return _themes_zsh(args.config, args.force)


def _summarize(session_id: str, wait_since: str) -> int:
    from cardak.core import summarize
    from cardak.core.summary import MODEL
    from cardak.llm.anthropic import AnthropicSummarizer

    return summarize.main([session_id, wait_since], lambda key: AnthropicSummarizer(key, MODEL))


def _deck(config_arg: str | None) -> int:
    import os

    from cardak.collectors import wezterm
    from cardak.core import spend
    from cardak.core.focus import focus_path, read_history
    from cardak.core.hook import log_problem
    from cardak.core.limits import limits_path
    from cardak.core.navigate import Navigator, Previewer
    from cardak.core.source import LiveSource
    from cardak.core.store import Store, StoreError, db_path
    from cardak.core.themes import SchemeStore
    from cardak.ui.app import DeckApp
    from cardak.wezterm.generate import module_version
    from cardak.wezterm.schemes import default_cache_dir, load_schemes, read_cached_schemes

    cache_dir = default_cache_dir()
    lua_dir = _lua_dir()
    store = SchemeStore(read_cached_schemes(cache_dir))
    try:
        agents: Store | None = Store(db_path())
    except StoreError as e:
        print(f"cardak: agent status unavailable: {e}", file=sys.stderr)
        agents = None
    try:
        log: Store | None = Store(db_path())  # its own connection: the UI thread reads it
    except StoreError:
        log = None
    counted = spend.background(lambda message: log_problem(os.environ, message))
    source = LiveSource(
        config_path(config_arg),
        spend=counted[0] if counted else lambda: None,
        limits_file=limits_path(),
        on_config=_theme_writer(lua_dir, config_dir(), store),
        schemes=store.get,
        store=agents,
        focus_history=lambda: read_history(focus_path()),
        key_file=anthropic_key_path(),
    )
    DeckApp(
        source,
        navigator=Navigator.live(lua_dir / "cardak.lua"),
        previewer=Previewer.live(),
        schemes=store,
        load_schemes=lambda: load_schemes(cache_dir),
        theme_status=lambda: _module_message(module_version(lua_dir / "cardak.lua")),
        hooks_status=_hooks_hint,
        resources_status=_resources_hint,
        log=log,
        send=lambda pane_id, text: wezterm.send_text(pane_id, text),
        skills_status=_skills_hint,
        open_tab=lambda cwd, near: wezterm.spawn(cwd, near),
    ).run()
    if counted is not None:
        counted[1].set()
    for db in (agents, log):
        if db is not None:
            db.close()
    return 0
