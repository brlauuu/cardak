"""Load ~/.config/cardak/projects.toml and append projects to it."""

import os
import re
import tempfile
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import tomli_w


@dataclass(frozen=True)
class Project:
    name: str
    path: Path
    theme: str | None = None
    accent: str | None = None
    github: str | None = None


@dataclass(frozen=True)
class Config:
    projects: tuple[Project, ...] = ()
    defaults: dict[str, object] = field(default_factory=dict)


class ConfigError(Exception):
    def __init__(self, path: Path, message: str, line: int | None = None) -> None:
        self.path = path
        self.message = message
        self.line = line
        where = f"{path.name} line {line}" if line else path.name
        super().__init__(f"{where}: {message}")


def config_path(cli_value: str | None = None, env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    if cli_value:
        return Path(cli_value).expanduser()
    if env.get("CARDAK_CONFIG"):
        return Path(env["CARDAK_CONFIG"]).expanduser()
    if env.get("XDG_CONFIG_HOME"):
        base = Path(env["XDG_CONFIG_HOME"])
    else:
        base = Path(env.get("HOME") or Path.home()) / ".config"
    return base / "cardak" / "projects.toml"


def _opt_str(entry: Mapping[str, object], key: str) -> str | None:
    value = entry.get(key)
    return value if isinstance(value, str) else None


def _read(path: Path) -> str | None:
    """The file's text, None when it does not exist; any other failure is a ConfigError."""
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as e:
        raise ConfigError(path, f"cannot read: {e}") from e


def _parse(path: Path, text: str) -> dict[str, object]:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        found = re.search(r"at line (\d+)", str(e))
        message = re.sub(r"\s*\(at line \d+, column \d+\)$", "", str(e))
        raise ConfigError(path, message, int(found.group(1)) if found else None) from e


def load(path: Path) -> Config:
    text = _read(path)
    if text is None:
        return Config()
    data = _parse(path, text)

    raw = data.get("project", [])
    if not isinstance(raw, list):
        raise ConfigError(path, "[[project]] must be an array of tables")
    projects = []
    for i, entry in enumerate(raw, 1):
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("name"), str)
            or not isinstance(entry.get("path"), str)
        ):
            raise ConfigError(path, f"project #{i} needs a string 'name' and 'path'")
        projects.append(
            Project(
                name=entry["name"],
                path=Path(entry["path"]).expanduser(),
                theme=_opt_str(entry, "theme"),
                accent=_opt_str(entry, "accent"),
                github=_opt_str(entry, "github"),
            )
        )
    defaults = data.get("defaults", {})
    if not isinstance(defaults, dict):
        raise ConfigError(path, "[defaults] must be a table")
    return Config(projects=tuple(projects), defaults=dict(defaults))


def _with_tilde(path: Path) -> str:
    home = Path.home()
    return f"~/{path.relative_to(home)}" if path.is_relative_to(home) else str(path)


def _appended(existing: str, block: str) -> str:
    if not existing or existing.endswith("\n\n"):
        return existing + block
    return existing + ("\n" if existing.endswith("\n") else "\n\n") + block


def _atomic_write(path: Path, target: Path, text: str) -> None:
    """Replace target with text via a temp file in its directory, keeping its mode."""
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        mode = target.stat().st_mode & 0o777 if target.exists() else 0o644
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".projects.", suffix=".toml")
    except OSError as e:
        raise ConfigError(path, f"cannot write: {e}") from e
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, mode)  # mkstemp creates 0600
        os.replace(tmp, target)
    except BaseException as e:
        Path(tmp).unlink(missing_ok=True)
        if isinstance(e, OSError):
            raise ConfigError(path, f"cannot write: {e}") from e
        raise


def add_project(path: Path, name: str, project_path: Path) -> None:
    """Append a [[project]] block, keeping the existing text (and its comments) as is."""
    config = load(path)  # raises ConfigError on a broken file, before anything is written
    project_path = project_path.expanduser()
    if any(p.path == project_path for p in config.projects):
        raise ConfigError(path, f"{project_path} is already configured")
    target = path.resolve()  # write through a symlink (e.g. a dotfiles repo), don't replace it
    # The header is written by hand: tomli-w emits an array of tables inline
    # (`project = [...]`), which appended after a table would land inside it.
    block = "[[project]]\n" + tomli_w.dumps({"name": name, "path": _with_tilde(project_path)})
    text = _appended(_read(target) or "", block)
    try:
        _parse(path, text)
    except ConfigError:
        # e.g. projects written inline (`project = [...]`) cannot take a [[project]] table
        raise ConfigError(path, "cannot append to this file's layout; add it by hand") from None
    _atomic_write(path, target, text)
