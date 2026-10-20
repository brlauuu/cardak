"""Install cardak.lua next to the user's WezTerm config. Never reads or edits wezterm.lua."""

import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Literal

MARKER = "-- cardak: generated"


@dataclass(frozen=True)
class InstallResult:
    path: Path
    status: Literal["installed", "updated", "unchanged"]


class InstallError(Exception):
    pass


def module_text() -> str:
    return files("cardak.wezterm").joinpath("cardak.lua").read_text(encoding="utf-8")


def default_dir(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    if env.get("XDG_CONFIG_HOME"):
        return Path(env["XDG_CONFIG_HOME"]) / "wezterm"
    return Path(env.get("HOME") or Path.home()) / ".config" / "wezterm"


def _existing(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as e:
        raise InstallError(f"cannot read {path}: {e}") from e


def _write(path: Path, text: str) -> None:
    """Atomically replace path with text, mode 0644."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".cardak.", suffix=".lua")
    except OSError as e:
        raise InstallError(f"cannot write {path}: {e}") from e
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except OSError as e:
        Path(tmp).unlink(missing_ok=True)
        raise InstallError(f"cannot write {path}: {e}") from e


def install(dest_dir: Path, force: bool = False) -> InstallResult:
    text = module_text()
    path = dest_dir / "cardak.lua"
    target = path.resolve()  # write through a symlink (e.g. a dotfiles repo), don't replace it
    existing = _existing(target)
    if existing == text:
        return InstallResult(path, "unchanged")
    if existing is not None and not existing.startswith(MARKER) and not force:
        raise InstallError(
            f"{path} exists and was not written by cardak; use --force to replace it"
        )
    _write(target, text)
    return InstallResult(path, "installed" if existing is None else "updated")


def snippet(dest_dir: Path, env: Mapping[str, str] | None = None) -> str:
    load = ""
    if dest_dir != default_dir(env):
        # Lua long brackets: no escaping needed for quotes or backslashes in the path
        load = f"package.path = [==[{dest_dir}/?.lua;]==] .. package.path\n"
    return (
        "Add this line to your wezterm.lua, before `return config`:\n\n"
        f"{load}require('cardak').apply(config)\n\n"
        "No wezterm.lua yet? This is a complete one:\n\n"
        "local wezterm = require 'wezterm'\n"
        "local config = wezterm.config_builder()\n"
        f"{load}require('cardak').apply(config)\n"
        "return config\n\n"
        "WezTerm reloads its config automatically.\n"
    )
