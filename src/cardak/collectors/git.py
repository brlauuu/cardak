"""Read-only git for recaps, the PM view and the project background: the top level, branches,
the remote, changed files, recent commits and the tracked files.

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
    more: bool = False  # more commits than the limit; `commits` holds the newest


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


def tracked_files(top: Path, runner: Runner = subprocess.run) -> tuple[str, ...] | None:
    """Every file git tracks under top, relative to it (where #9 looks for a logo)."""
    out = _git(top, ["ls-files", "-z"], runner)
    return tuple(f for f in out.split("\0") if f) if out is not None else None


def branch(path: Path, runner: Runner = subprocess.run) -> str | None:
    """The branch checked out in path's worktree; None when detached or outside git."""
    out = _git(path, ["branch", "--show-current"], runner)
    return out.strip() or None if out is not None else None


def branches(top: Path, runner: Runner = subprocess.run) -> tuple[str, ...] | None:
    """Every local branch; worktrees share them, so any of a repo's worktrees gives all."""
    out = _git(top, ["for-each-ref", "--format=%(refname:short)", "refs/heads"], runner)
    if out is None:
        return None
    return tuple(line.strip() for line in out.splitlines() if line.strip())


def remote_url(top: Path, runner: Runner = subprocess.run) -> str | None:
    out = _git(top, ["remote", "get-url", "origin"], runner)
    return out.strip() or None if out is not None else None


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
    """Branch, changed / untracked counts, and up to `limit` commits since `since` (none when it
    is None). One more is asked for, so `more` tells a list cut at the limit from a full one."""
    status = _git(top, ["status", "--porcelain"], runner)
    branch = _git(top, ["branch", "--show-current"], runner)
    if status is None or branch is None:
        return None
    lines = status.splitlines()
    untracked = sum(line.startswith("??") for line in lines)
    commits: tuple[Commit, ...] = ()
    if since is not None:
        commits = _commits(top, since, limit + 1, runner) or ()
    more = len(commits) > limit
    return GitState(
        branch.strip() or None, len(lines) - untracked, untracked, commits[:limit], more
    )
