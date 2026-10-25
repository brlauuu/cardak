"""Install and remove cardak's hooks in Claude Code's settings.json.

The only cardak code that writes under ~/.claude/. It changes nothing but cardak's own
hook entries (`<path>/cardak hook`), backs the file up before writing, and imports
nothing from cardak.
"""

import copy
import json
import os
import shlex
import shutil
import tempfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "PermissionRequest",
    "Notification",
    "Stop",
    "SessionEnd",
)
TIMEOUT = 5  # seconds; the hook takes ~0.1 s

HookStatus = Literal["installed", "partial", "missing"]
Settings = dict[str, Any]


class SettingsError(Exception):
    pass


def default_settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def hook_command(exe: Path) -> str:
    return f"{shlex.quote(str(exe))} hook"


def is_cardak(command: str) -> bool:
    try:
        argv = shlex.split(command)
    except ValueError:
        return False
    return len(argv) == 2 and Path(argv[0]).name == "cardak" and argv[1] == "hook"


def _hooks_table(settings: Settings) -> dict[str, Any]:
    hooks = settings.get("hooks", {})
    if not isinstance(hooks, dict) or not all(isinstance(v, list) for v in hooks.values()):
        raise SettingsError('"hooks" in settings.json is not an object of lists')
    return hooks


def _cardak_commands(group: object) -> list[str]:
    if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
        return []
    return [
        h["command"]
        for h in group["hooks"]
        if isinstance(h, dict) and isinstance(h.get("command"), str) and is_cardak(h["command"])
    ]


def _without_cardak(group: object) -> object | None:
    """The group minus cardak's entries; None when nothing else was in it."""
    if not _cardak_commands(group):
        return group
    assert isinstance(group, dict)
    rest = [
        h
        for h in group["hooks"]
        if not (isinstance(h, dict) and is_cardak(str(h.get("command", ""))))
    ]
    return {**group, "hooks": rest} if rest else None


def without_hooks(settings: Settings) -> Settings:
    out = copy.deepcopy(settings)
    if "hooks" not in out:
        return out
    hooks = _hooks_table(out)
    for event in list(hooks):
        groups = [g for g in (_without_cardak(g) for g in hooks[event]) if g is not None]
        if groups:
            hooks[event] = groups
        else:
            del hooks[event]
    if not hooks:
        del out["hooks"]
    return out


def with_hooks(settings: Settings, command: str) -> Settings:
    out = without_hooks(settings)
    hooks = _hooks_table(out)
    for event in EVENTS:
        hooks.setdefault(event, []).append(
            {"hooks": [{"type": "command", "command": command, "timeout": TIMEOUT}]}
        )
    out["hooks"] = hooks
    return out


def _commands_by_event(settings: Settings) -> dict[str, list[str]]:
    hooks = _hooks_table(settings)
    return {
        event: [c for g in groups for c in _cardak_commands(g)] for event, groups in hooks.items()
    }


def status(settings: Settings, command: str) -> HookStatus:
    found = _commands_by_event(settings)
    if all(command in found.get(event, []) for event in EVENTS):
        return "installed"
    return "partial" if any(found.values()) else "missing"


def changes(before: Settings, after: Settings) -> list[str]:
    old, new = _commands_by_event(before), _commands_by_event(after)
    lines = []
    for event in [*EVENTS, *sorted(set(old) | set(new) - set(EVENTS))]:
        was, now = old.get(event, []), new.get(event, [])
        lines += [f"- {event} ({c})" for c in was if c not in now]
        lines += [f"+ {event}" for c in now if c not in was]
    return lines


def load_settings(path: Path) -> Settings:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError) as e:
        raise SettingsError(f"cannot read {path}: {e}") from e
    try:
        data = json.loads(text)
    except ValueError as e:
        raise SettingsError(f"{path} is not valid JSON ({e}); fix it first") from e
    if not isinstance(data, dict):
        raise SettingsError(f"{path} is not a JSON object")
    return data


def backup_path(path: Path, now: datetime) -> Path:
    return path.with_name(f"{path.name}.cardak-backup-{now:%Y%m%d-%H%M%S}")


def save_settings(path: Path, data: Settings, now: datetime) -> Path | None:
    """Write data (atomic, through a symlink, mode kept); return the backup of the old file."""
    target = path.resolve()
    backup, indent = None, 2
    if target.exists():
        indent = _indent(target.read_text(encoding="utf-8"))
        backup = backup_path(path, now)
        shutil.copy2(target, backup)
    text = json.dumps(data, indent=indent, ensure_ascii=False) + "\n"
    write_atomic(target, text, 0o600)
    return backup


def _indent(text: str) -> int | str:
    """The file's own indent (spaces or a tab), so a rewrite changes only cardak's entries.

    Claude Code writes 2 spaces, which round-trips byte for byte; hand formatting such as an
    inline array is expanded, its content kept."""
    for line in text.splitlines()[1:]:
        if line.startswith("\t"):
            return "\t"
        spaces = len(line) - len(line.lstrip(" "))
        if spaces:
            return spaces
    return 2


def write_atomic(target: Path, text: str, new_mode: int) -> None:
    """Replace target via a temp file beside it; keep its mode (a new file gets new_mode)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    mode = target.stat().st_mode & 0o777 if target.exists() else new_mode
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def find_executable(argv0: str, which: Callable[[str], str | None] | None = None) -> Path | None:
    """The cardak to register: the one running now, else the one on PATH."""
    if Path(argv0).name == "cardak":
        return Path(argv0).absolute()  # not resolved: keep the stable ~/.local/bin link
    found = (which or shutil.which)("cardak")
    return Path(found) if found else None
