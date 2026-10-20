"""Command-line entry point: `cardak [--config PATH] [tui]`, `cardak wezterm install`."""

import argparse
import sys
from importlib.metadata import version
from pathlib import Path

from cardak.core.config import config_path
from cardak.core.navigate import Navigator
from cardak.core.source import LiveSource
from cardak.ui.app import DeckApp
from cardak.wezterm.install import InstallError, default_dir, install, snippet


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cardak",
        description="Project-centric deck for WezTerm panes and Claude Code sessions.",
    )
    parser.add_argument("--version", action="version", version=f"cardak {version('cardak')}")
    parser.add_argument(
        "--config",
        help="projects.toml to use (default: $CARDAK_CONFIG, else ~/.config/cardak/projects.toml)",
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("tui", help="open the deck (the default)")
    wez = sub.add_parser("wezterm", help="WezTerm integration")
    wez_sub = wez.add_subparsers(dest="wezterm_command", required=True)
    inst = wez_sub.add_parser("install", help="install cardak.lua so jumps raise windows")
    inst.add_argument("--dir", type=Path, help="target directory (default: ~/.config/wezterm)")
    inst.add_argument(
        "--force", action="store_true", help="replace a cardak.lua not written by cardak"
    )
    return parser


def _wezterm_install(dest: Path | None, force: bool) -> int:
    dest_dir = dest or default_dir()
    try:
        result = install(dest_dir, force=force)
    except InstallError as e:
        print(f"cardak: {e}", file=sys.stderr)
        return 1
    print(f"{result.status} {result.path}\n")
    print(snippet(dest_dir), end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "wezterm":
        return _wezterm_install(args.dir, args.force)
    lua_file = default_dir() / "cardak.lua"
    DeckApp(LiveSource(config_path(args.config)), navigator=Navigator.live(lua_file)).run()
    return 0
