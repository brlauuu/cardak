"""Map a pane's cwd to a project: configured path first, then git root, else Other."""

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from cardak.core.config import Config, real_path

Kind = Literal["config", "git", "other"]
GitLookup = Callable[[Path], Path | None]


@dataclass(frozen=True)
class Resolved:
    kind: Kind
    key: Path | None
    name: str
    accent: str | None = None
    order: int = 0
    theme: str | None = None


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
        # Each project under its configured path and, if it's a symlink, the real one too: a
        # pane's cwd can come from /proc (resolved) or OSC 7 (the shell's logical path) (#14).
        entries = [
            (match, order, project)
            for order, project in enumerate(config.projects)
            for match in dict.fromkeys((project.path, real_path(project.path)))
        ]
        # Longest path first so a nested project wins over its parent.
        self._projects = sorted(entries, key=lambda e: len(e[0].parts), reverse=True)
        self._git = git
        self._git_cache: dict[Path, Path | None] = {}
        self._real_cache: dict[Path, Path] = {}

    def _configured(self, cwd: Path) -> Resolved | None:
        for match, order, project in self._projects:
            if cwd.is_relative_to(match):
                return Resolved(
                    "config", project.path, project.name, project.accent, order, project.theme
                )
        return None

    def resolve(self, cwd: Path | None) -> Resolved:
        if cwd is None:
            return OTHER
        found = self._configured(cwd)
        if found is None:
            if cwd not in self._real_cache:
                self._real_cache[cwd] = real_path(cwd)
            if (real := self._real_cache[cwd]) != cwd:
                found = self._configured(real)
        if found is not None:
            return found
        if cwd not in self._git_cache:
            self._git_cache[cwd] = self._git(cwd)
        root = self._git_cache[cwd]
        return OTHER if root is None else Resolved("git", root, root.name)
