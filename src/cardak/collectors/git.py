"""Read-only git for recaps: the top level, the branch, changed files and recent commits.

Every call is `git --no-optional-locks -C <dir>` with a timeout; any failure is None.
"""

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

Runner = Callable[..., subprocess.CompletedProcess[str]]
TIMEOUT = 3
_SEP = "\x1f"


@dataclass(frozen=True)
class Commit:
    sha: str
    ts: float
    subject: str


@dataclass(frozen=True)
class GitState:
    branch: str | None  # None: detached
    changed: int
    untracked: int
    commits: tuple[Commit, ...]  # newest first


def _git(path: Path, args: list[str], runner: Runner) -> str | None:
    try:
        out = runner(
            ["git", "--no-optional-locks", "-C", str(path), *args],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout if out.returncode == 0 else None


def toplevel(path: Path, runner: Runner = subprocess.run) -> Path | None:
    """The git top level of path (a worktree's own), or None outside git."""
    out = _git(path, ["rev-parse", "--show-toplevel"], runner)
    return Path(out.strip()) if out and out.strip() else None


def _commits(top: Path, since: float, limit: int, runner: Runner) -> tuple[Commit, ...] | None:
    out = _git(
        top,
        ["log", "-n", str(limit), f"--since=@{int(since)}", f"--format=%h{_SEP}%ct{_SEP}%s"],
        runner,
    )
    if out is None:
        return None
    commits = []
    for line in out.splitlines():
        sha, ts, subject = (line.split(_SEP, 2) + ["", ""])[:3]
        if sha and ts.isdigit():
            commits.append(Commit(sha, float(ts), subject))
    return tuple(commits)


def state(
    top: Path, since: float | None, limit: int = 20, runner: Runner = subprocess.run
) -> GitState | None:
    """Branch, changed / untracked counts, and commits since `since` (none when it is None)."""
    status = _git(top, ["status", "--porcelain"], runner)
    branch = _git(top, ["branch", "--show-current"], runner)
    if status is None or branch is None:
        return None
    lines = status.splitlines()
    untracked = sum(line.startswith("??") for line in lines)
    commits: tuple[Commit, ...] = ()
    if since is not None:
        commits = _commits(top, since, limit, runner) or ()
    return GitState(branch.strip() or None, len(lines) - untracked, untracked, commits)
