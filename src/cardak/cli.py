"""Command-line entry point: `cardak [--config PATH] [tui]`."""

import argparse
from importlib.metadata import version

from cardak.core.config import config_path
from cardak.core.source import LiveSource
from cardak.ui.app import DeckApp


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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command in (None, "tui"):
        DeckApp(LiveSource(config_path(args.config))).run()
    return 0
