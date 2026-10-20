"""Command-line entry point."""

import argparse
from importlib.metadata import version


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cardak",
        description="Project-centric deck for WezTerm panes and Claude Code sessions.",
    )
    parser.add_argument("--version", action="version", version=f"cardak {version('cardak')}")
    return parser


def main(argv: list[str] | None = None) -> int:
    build_parser().parse_args(argv)
    return 0
