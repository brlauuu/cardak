"""`/checkpoint` and `/where-were-we`: the Claude Code skills cardak ships, and their installer.

They go to `~/.claude/skills/<name>/SKILL.md`. cardak writes and removes only files carrying
MARKER (the line after the frontmatter); anything else there is the user's.
"""

from importlib.resources import files
from pathlib import Path
from typing import Literal

from cardak.claude.install import write_atomic

NAMES = ("checkpoint", "where-were-we")
MARKER = "<!-- cardak: generated -->"
FILE = "SKILL.md"
HINT = "Run `cardak skills install` to add /checkpoint and /where-were-we to Claude Code."

SkillStatus = Literal["installed", "outdated", "missing", "foreign"]


class SkillsError(Exception):
    pass


def default_dir() -> Path:
    return Path.home() / ".claude" / "skills"


def bundled(name: str) -> str:
    return (files("cardak.claude") / "skills" / name / FILE).read_text(encoding="utf-8")


def _is_cardak(text: str) -> bool:
    lines = text.splitlines()
    if not lines or lines[0] != "---" or "---" not in lines[1:]:
        return False
    end = lines.index("---", 1)
    return lines[end + 1 : end + 2] == [MARKER]


def status(dir: Path, name: str) -> SkillStatus:
    try:
        text = (dir / name / FILE).read_text(encoding="utf-8")
    except FileNotFoundError:
        return "missing"
    except (OSError, UnicodeDecodeError):
        return "foreign"
    if not _is_cardak(text):
        return "foreign"
    return "installed" if text == bundled(name) else "outdated"


def install(dir: Path, force: bool) -> list[Path]:
    """Write the skills that differ; refuse (writing nothing) over foreign files unless force."""
    states = {name: status(dir, name) for name in NAMES}
    foreign = [dir / n / FILE for n, s in states.items() if s == "foreign"]
    if foreign and not force:
        paths = ", ".join(str(p) for p in foreign)
        raise SkillsError(f"not written by cardak (use --force to replace): {paths}")
    written = []
    for name, state in states.items():
        if state != "installed":
            path = dir / name / FILE
            write_atomic(path.resolve() if path.exists() else path, bundled(name), 0o644)
            written.append(path)
    return written


def uninstall(dir: Path) -> list[Path]:
    """Remove cardak's skill files, then their directories if that empties them."""
    removed = []
    for name in NAMES:
        if status(dir, name) in ("installed", "outdated"):
            path = dir / name / FILE
            path.unlink()
            removed.append(path)
            if not any(path.parent.iterdir()):
                path.parent.rmdir()
    return removed


def hint(dir: Path) -> str | None:
    """The deck's hint when a skill is missing (someone else's file of that name counts)."""
    return HINT if any(status(dir, n) == "missing" for n in NAMES) else None
