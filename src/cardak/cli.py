"""Command-line entry point: `cardak [--config PATH] [tui]`, `cardak wezterm install`, ….

`cardak hook` runs on every Claude Code hook, so the deck's heavy imports (Textual, psutil)
are deferred to the commands that need them.
"""

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from cardak.core.config import Config, ConfigError, config_dir, config_path, load
from cardak.core.themes import SchemeStore
from cardak.wezterm.generate import (
    MODULE_VERSION,
    NO_MODULE_MSG,
    OLD_MODULE_MSG,
    GenerateError,
    install_tint_hook,
    module_version,
    write_generated,
)
from cardak.wezterm.install import InstallError, default_dir, install, snippet
from cardak.wezterm.schemes import (
    SchemesUnavailable,
    default_cache_dir,
    load_schemes,
    read_cached_schemes,
)


class _Version(argparse.Action):
    """--version, reading the package metadata only when asked (it is slow to import)."""

    def __call__(self, parser: argparse.ArgumentParser, *_args: object) -> None:
        from importlib.metadata import version

        print(f"cardak {version('cardak')}")
        parser.exit()


def build_parser() -> argparse.ArgumentParser:
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


def _hooks(command: str, settings_arg: Path | None, yes: bool) -> int:
    from datetime import datetime

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
            state = claude.status(before, hook)
            print({"installed": "installed", "partial": "partly installed (run `cardak hooks "
                   "install`)", "missing": "not installed"}[state] + f": {path}")  # fmt: skip
            return 0
        after = (
            claude.with_hooks(before, hook)
            if command == "install"
            else claude.without_hooks(before)
        )
    except claude.SettingsError as e:
        print(f"cardak: {e}", file=sys.stderr)
        return 1
    if after == before:
        print(f"cardak hooks already {'installed' if command == 'install' else 'absent'}: {path}")
        return 0
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
    if command == "install":
        print("Done. New Claude Code sessions report their status to the deck.")
    else:
        print("Done.")
    return 0


def _wezterm_install(dest: Path | None, force: bool) -> int:
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
    if version is None:
        return NO_MODULE_MSG
    if version < MODULE_VERSION:
        return OLD_MODULE_MSG
    return None


def _theme_writer(
    wezterm_dir: Path, cardak_dir: Path, store: SchemeStore
) -> Callable[[Config], list[str]]:
    """What the deck runs after every config change: regenerate, return banner warnings."""

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
    args = build_parser().parse_args(argv)
    if args.command == "hook":
        from cardak.core import hook

        return hook.main(sys.stdin)
    if args.command == "hooks":
        return _hooks(args.hooks_command, args.settings, getattr(args, "yes", False))
    if args.command == "wezterm":
        return _wezterm_install(args.dir, args.force)
    if args.command == "themes":
        if args.themes_command == "apply":
            return _themes_apply(args.config)
        return _themes_zsh(args.config, args.force)
    return _deck(args.config)


def _deck(config_arg: str | None) -> int:
    from cardak.core.navigate import Navigator, Previewer
    from cardak.core.source import LiveSource
    from cardak.ui.app import DeckApp

    cache_dir = default_cache_dir()
    store = SchemeStore(read_cached_schemes(cache_dir))
    source = LiveSource(
        config_path(config_arg),
        on_config=_theme_writer(default_dir(), config_dir(), store),
        schemes=store.get,
    )
    DeckApp(
        source,
        navigator=Navigator.live(default_dir() / "cardak.lua"),
        previewer=Previewer.live(),
        schemes=store,
        load_schemes=lambda: load_schemes(cache_dir),
        theme_status=lambda: _module_message(module_version(default_dir() / "cardak.lua")),
    ).run()
    return 0
