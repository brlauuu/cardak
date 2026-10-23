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
    anthropic_key_path,
    config_dir,
    config_path,
    load,
)

if TYPE_CHECKING:
    import argparse

    from cardak.core.themes import SchemeStore


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
    return _write_settings(path, before, after, yes, install)


def _write_settings(path: Path, before: dict, after: dict, yes: bool, install: bool) -> int:
    """Show what changes, ask (unless --yes), back up and write settings.json."""
    from datetime import datetime

    from cardak.claude import install as claude

    print(f"{path}:")
    print("\n".join(f"  {line}" for line in claude.changes(before, after)))
    now = datetime.now()
    if path.exists():
        print(f"A backup goes to {claude.backup_path(path, now)}")
    if not yes and input(f"Write {path}? [y/N] ").strip().lower() not in ("y", "yes"):
        print("Nothing written.")
        return 0
    try:
        claude.save_settings(path, after, now)
    except OSError as e:
        print(f"cardak: cannot write {path}: {e}", file=sys.stderr)
        return 1
    print("Done. New Claude Code sessions report their status to the deck." if install else "Done.")
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
    if not yes and input(f"Write {path} and start it? [y/N] ").strip().lower() not in ("y", "yes"):
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
    print(f"{result.status} {result.path}\n")
    print(snippet(dest_dir), end="")
    return 0


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
    from cardak.wezterm.install import default_dir
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
    result = write_generated(default_dir(), config_dir(), config, schemes)
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
    from cardak.wezterm.install import default_dir
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
    write_generated(default_dir(), cardak_dir, config, read_cached_schemes(default_cache_dir()))
    print(f"installed {hook}\n\nAdd this line to your ~/.zshrc:\n\nsource {hook}\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    if (sys.argv[1:] if argv is None else argv) == ["hook"]:
        from cardak.core import hook

        return hook.main(sys.stdin)
    args = build_parser().parse_args(argv)
    if args.command == "daemon":
        from cardak.core import daemon

        return daemon.main()
    if args.command == "summarize":
        return _summarize(args.session_id, args.wait_since)
    if args.command == "hooks":
        return _hooks(args.hooks_command, args.settings, getattr(args, "yes", False))
    if args.command in ("resources", "wezterm", "themes"):
        return _setup(args)
    return _deck(args.config)


def _setup(args: argparse.Namespace) -> int:
    """The commands that install or generate files: resources, wezterm, themes."""
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
    from cardak.core.focus import focus_path, read_history
    from cardak.core.navigate import Navigator, Previewer
    from cardak.core.source import LiveSource
    from cardak.core.store import Store, StoreError, db_path
    from cardak.core.themes import SchemeStore
    from cardak.ui.app import DeckApp
    from cardak.wezterm.generate import module_version
    from cardak.wezterm.install import default_dir
    from cardak.wezterm.schemes import default_cache_dir, load_schemes, read_cached_schemes

    cache_dir = default_cache_dir()
    store = SchemeStore(read_cached_schemes(cache_dir))
    try:
        agents: Store | None = Store(db_path())
    except StoreError as e:
        print(f"cardak: agent status unavailable: {e}", file=sys.stderr)
        agents = None
    source = LiveSource(
        config_path(config_arg),
        on_config=_theme_writer(default_dir(), config_dir(), store),
        schemes=store.get,
        store=agents,
        focus_history=lambda: read_history(focus_path()),
        key_file=anthropic_key_path(),
    )
    DeckApp(
        source,
        navigator=Navigator.live(default_dir() / "cardak.lua"),
        previewer=Previewer.live(),
        schemes=store,
        load_schemes=lambda: load_schemes(cache_dir),
        theme_status=lambda: _module_message(module_version(default_dir() / "cardak.lua")),
        hooks_status=_hooks_hint,
        resources_status=_resources_hint,
    ).run()
    if agents is not None:
        agents.close()
    return 0
