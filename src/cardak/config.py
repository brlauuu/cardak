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


def load(path: Path) -> Config:
    try:
        text = path.read_text()
    except FileNotFoundError:
        return Config()
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        found = re.search(r"at line (\d+)", str(e))
        message = re.sub(r"\s*\(at line \d+, column \d+\)$", "", str(e))
        raise ConfigError(path, message, int(found.group(1)) if found else None) from e

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


def add_project(path: Path, name: str, project_path: Path) -> None:
    """Append a [[project]] block, keeping the existing text (and its comments) as is."""
    config = load(path)  # raises ConfigError on a broken file, before anything is written
    project_path = project_path.expanduser()
    if any(p.path == project_path for p in config.projects):
        raise ConfigError(path, f"{project_path} is already configured")
    existing = path.read_text() if path.exists() else ""
    # The header is written by hand: tomli-w emits an array of tables inline
    # (`project = [...]`), which appended after a table would land inside it.
    block = "[[project]]\n" + tomli_w.dumps({"name": name, "path": _with_tilde(project_path)})
    if not existing or existing.endswith("\n\n"):
        sep = ""
    elif existing.endswith("\n"):
        sep = "\n"
    else:
        sep = "\n\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".projects.", suffix=".toml")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(existing + sep + block)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
