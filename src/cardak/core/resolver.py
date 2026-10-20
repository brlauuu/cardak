"""Map a pane's cwd to a project: configured path first, then git root, else Other."""

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from cardak.core.config import Config

Kind = Literal["config", "git", "other"]
GitLookup = Callable[[Path], Path | None]


@dataclass(frozen=True)
class Resolved:
    kind: Kind
    key: Path | None
    name: str
    accent: str | None = None
    order: int = 0


OTHER = Resolved("other", None, "Other")


def run_git(cwd: Path) -> Path | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=1,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    top = out.stdout.strip()
    return Path(top) if out.returncode == 0 and top else None


class ProjectResolver:
    def __init__(self, config: Config, git: GitLookup = run_git) -> None:
        indexed = list(enumerate(config.projects))
        # Longest path first so a nested project wins over its parent.
        self._projects = sorted(indexed, key=lambda item: len(item[1].path.parts), reverse=True)
        self._git = git
        self._git_cache: dict[Path, Path | None] = {}

    def resolve(self, cwd: Path | None) -> Resolved:
        if cwd is None:
            return OTHER
        for order, project in self._projects:
            if cwd.is_relative_to(project.path):
                return Resolved("config", project.path, project.name, project.accent, order)
        if cwd not in self._git_cache:
            self._git_cache[cwd] = self._git(cwd)
        root = self._git_cache[cwd]
        return OTHER if root is None else Resolved("git", root, root.name)
