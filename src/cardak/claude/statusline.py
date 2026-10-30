"""Wrap and unwrap the user's status line in Claude Code's settings.json (#70).

`statusLine.command` becomes `<cardak> statusline --then '<the user's command>'`, so the original
lives in the file itself and uninstall puts it back exactly. Nothing else in `statusLine` or the
file changes. A workaround until Claude Code offers another way to read plan limits (#97).
"""

import copy
import shlex
from pathlib import Path

from cardak.claude.install import Settings, SettingsError


def wrapped_command(exe: Path, original: str | None) -> str:
    command = f"{shlex.quote(str(exe))} statusline"
    return command if original is None else f"{command} --then {shlex.quote(original)}"


def unwrap(command: str) -> tuple[bool, str | None]:
    """(True, the wrapped command or None) for cardak's command, else (False, None)."""
    try:
        argv = shlex.split(command)
    except ValueError:
        return False, None
    if len(argv) < 2 or Path(argv[0]).name != "cardak" or argv[1] != "statusline":
        return False, None
    if len(argv) == 2:
        return True, None
    if len(argv) == 4 and argv[2] == "--then":
        return True, argv[3]
    return False, None


def _entry(settings: Settings) -> dict | None:
    entry = settings.get("statusLine")
    if entry is None:
        return None
    if not isinstance(entry, dict) or entry.get("type") != "command":
        raise SettingsError(
            '"statusLine" in settings.json is not a command; cardak leaves it alone'
        )
    return entry


def _command(settings: Settings) -> str | None:
    entry = settings.get("statusLine")
    command = entry.get("command") if isinstance(entry, dict) else None
    return command if isinstance(command, str) else None


def with_statusline(settings: Settings, exe: Path) -> Settings:
    entry = _entry(settings)
    if entry is None:
        return {
            **settings,
            "statusLine": {"type": "command", "command": wrapped_command(exe, None)},
        }
    current = entry.get("command")
    if isinstance(current, str) and unwrap(current)[0]:
        return settings
    original = current if isinstance(current, str) and current else None
    out = copy.deepcopy(settings)
    out["statusLine"]["command"] = wrapped_command(exe, original)
    return out


def without_statusline(settings: Settings) -> Settings:
    command = _command(settings)
    ours, original = unwrap(command) if command is not None else (False, None)
    if not ours:
        return settings
    out = copy.deepcopy(settings)
    if original is None:
        del out["statusLine"]
    else:
        out["statusLine"]["command"] = original
    return out


def statusline_status(settings: Settings) -> str | None:
    """The command cardak wraps ("" when none), or None when cardak's isn't installed."""
    command = _command(settings)
    ours, original = unwrap(command) if command is not None else (False, None)
    return (original or "") if ours else None


def changes(before: Settings, after: Settings) -> list[str]:
    old, new = _command(before), _command(after)
    lines = [f"- statusLine.command: {old}"] if old is not None and old != new else []
    return lines + ([f"+ statusLine.command: {new}"] if new is not None and new != old else [])
