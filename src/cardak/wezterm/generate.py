"""Generate the files cardak's WezTerm module and zsh hook read, from projects.toml."""

import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from cardak.core.config import Config, Project, real_path
from cardak.core.themes import Scheme, project_tint
from cardak.wezterm.files import write_atomic
from cardak.wezterm.install import MARKER

MODULE_VERSION = 4
ZSH_MARKER = "# cardak: generated"
PROJECTS_FILE = "cardak_projects.lua"
TINTS_FILE = "tints"
HOOK_FILE = "tint.zsh"
NO_MODULE_MSG = (
    "Themes need cardak's WezTerm module: run `cardak wezterm install` "
    "and add the line it prints to wezterm.lua."
)
OLD_MODULE_MSG = (
    "Your cardak.lua is from an older cardak; run `cardak wezterm install` to update it."
)


class GenerateError(Exception):
    pass


@dataclass(frozen=True)
class GenerateResult:
    written: tuple[Path, ...]
    problems: tuple[str, ...]
    module_version: int | None


def lua_str(s: str) -> str:
    """A Lua long-bracket string: no escaping, whatever quotes or backslashes s holds."""
    level = 0
    # A trailing "]" + level "=" would merge with the closing bracket, so it also bumps the level.
    while f"]{'=' * level}]" in s or s.endswith(f"]{'=' * level}"):
        level += 1
    eq = "=" * level
    return f"[{eq}[{s}]{eq}]"


def module_version(lua_file: Path) -> int | None:
    """Version of an installed cardak.lua: None if absent or not cardak's, 1 before M3."""
    try:
        head = lua_file.read_text(encoding="utf-8").splitlines()[:3]
    except (OSError, UnicodeDecodeError):
        return None
    if not head or not head[0].startswith(MARKER):
        return None
    for line in head[1:]:
        if m := re.fullmatch(r"-- cardak-module: (\d+)", line):
            return int(m.group(1))
    return 1


def _first_per_path(config: Config) -> tuple[list[Project], list[str]]:
    """One project per directory, the first listed, as the deck's resolver picks it (#23).
    Two paths that resolve to the same directory (a symlink and its target, #14) count."""
    kept: dict[Path, Project] = {}
    problems: list[str] = []
    for p in config.projects:
        real = real_path(p.path)
        if real in kept:
            first = kept[real].name
            problems.append(f"duplicate path {p.path}: {p.name} is ignored, {first} is used")
        else:
            kept[real] = p
    return list(kept.values()), problems


def _tintable(p: Project) -> bool:
    """tints is `path<TAB>color` per line: a tab or newline in the path would break it (#28)."""
    return not any(c in str(p.path) for c in "\t\n\r")


def tint_problems(config: Config) -> list[str]:
    return [
        f"{p.name}: its path can't go in the tints file (tab or newline)"
        for p in _first_per_path(config)[0]
        if not _tintable(p)
    ]


def render_projects_lua(config: Config, known: Collection[str] | None) -> tuple[str, list[str]]:
    projects, problems = _first_per_path(config)
    lines = [f"{MARKER} by cardak from projects.toml; edits are overwritten.", "return {"]
    default = config.defaults.get("theme")
    if isinstance(default, str):
        if known is not None and default not in known:
            problems.append(f'unknown scheme "{default}" in [defaults]')
        else:
            lines.append(f"  default = {lua_str(default)},")
    lines.append("  projects = {")
    for p in projects:
        # every project, themed or not: the module names tabs after it (#31)
        scheme = ""
        if p.theme is not None and known is not None and p.theme not in known:
            problems.append(f'unknown scheme "{p.theme}" for {p.name}')
        elif p.theme is not None:
            scheme = f", scheme = {lua_str(p.theme)}"
        # a symlinked project under its real path too: WezTerm may see either (#14)
        for path in dict.fromkeys((str(p.path), str(real_path(p.path)))):
            lines.append(f"    {{ path = {lua_str(path)}, name = {lua_str(p.name)}{scheme} }},")
    lines += ["  },", "}"]
    return "\n".join(lines) + "\n", problems


def render_tints(config: Config, schemes: Mapping[str, Scheme]) -> str:
    lines = [f"{ZSH_MARKER} from projects.toml; edits are overwritten."]
    for p in _first_per_path(config)[0]:
        if _tintable(p) and (color := project_tint(p, schemes)):
            paths = dict.fromkeys((str(p.path), str(real_path(p.path))))
            lines += [f"{path}\t{color}" for path in paths]
    return "\n".join(lines) + "\n"


def _write_if_changed(path: Path, text: str, marker: str, force: bool = False) -> bool:
    target = path.resolve()
    try:
        old: str | None = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        old = None
    except (OSError, UnicodeDecodeError) as e:
        raise GenerateError(f"cannot read {path}: {e}") from e
    if old == text:
        return False
    if old is not None and not old.startswith(marker) and not force:
        raise GenerateError(f"{path} was not written by cardak; not replacing it (use --force)")
    try:
        write_atomic(target, text)
    except OSError as e:
        raise GenerateError(f"cannot write {path}: {e}") from e
    return True


def write_generated(
    wezterm_dir: Path, cardak_dir: Path, config: Config, schemes: Mapping[str, Scheme] | None
) -> GenerateResult:
    written: list[Path] = []
    problems: list[str] = []
    version = module_version(wezterm_dir / "cardak.lua")
    jobs: list[tuple[Path, str, str]] = []
    if version is not None:
        text, found = render_projects_lua(config, set(schemes) if schemes else None)
        problems += found
        jobs.append((wezterm_dir / PROJECTS_FILE, text, MARKER))
    jobs.append((cardak_dir / TINTS_FILE, render_tints(config, schemes or {}), ZSH_MARKER))
    problems += tint_problems(config)
    for path, text, marker in jobs:
        try:
            if _write_if_changed(path, text, marker):
                written.append(path)
        except GenerateError as e:
            problems.append(str(e))
    return GenerateResult(tuple(written), tuple(problems), version)


def install_tint_hook(cardak_dir: Path, force: bool = False) -> Path:
    path = cardak_dir / HOOK_FILE
    text = files("cardak.wezterm").joinpath(HOOK_FILE).read_text(encoding="utf-8")
    _write_if_changed(path, text, ZSH_MARKER, force=force)
    return path
