"""Per-tty process trees and their CPU/RAM. PsutilTable is the only per-process /proc reader."""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import psutil


@dataclass(frozen=True)
class ProcInfo:
    pid: int
    ppid: int
    tty: str | None
    pgid: int
    tpgid: int
    name: str
    cmdline: tuple[str, ...]
    create_time: float
    cpu_pct: float | None  # None on a process's first sample
    rss: int


class ProcessTable(Protocol):
    def snapshot(self) -> list[ProcInfo]: ...


class PsutilTable:
    """Keeps psutil.Process objects between snapshots: cpu_percent needs two samples."""

    def __init__(self, proc_root: Path = Path("/proc"), dev_root: Path = Path("/dev")) -> None:
        self._proc_root = proc_root
        self._dev_root = dev_root
        self._seen: dict[tuple[int, float], psutil.Process] = {}

    def _stat(self, pid: int) -> tuple[int, int, int]:
        """(pgrp, tty_nr, tpgid) from /proc/<pid>/stat; the comm field may contain spaces."""
        stat = (self._proc_root / str(pid) / "stat").read_text()
        fields = stat[stat.rindex(")") + 2 :].split()
        return int(fields[2]), int(fields[4]), int(fields[5])

    def _terminal_map(self) -> dict[int, str]:
        """Device number -> tty path, rebuilt per snapshot.

        psutil's own map (behind Process.terminal) is memoized on first use, so a pane
        opened after the deck started would never get a tty.
        """
        ttys: dict[int, str] = {}
        for path in [*self._dev_root.glob("tty*"), *self._dev_root.glob("pts/*")]:
            try:
                ttys[path.stat().st_rdev] = str(path)
            except OSError:
                continue
        return ttys

    def _read(
        self,
        proc: psutil.Process,
        ttys: dict[int, str],
        seen: dict[tuple[int, float], psutil.Process],
    ) -> ProcInfo:
        """One process; records it in `seen`. Raises psutil.Error/OSError if it vanished."""
        info = proc.info
        key = (info["pid"], info["create_time"])
        known = self._seen.get(key)
        tracked = known or proc
        cpu = tracked.cpu_percent(None)
        pgid, tty_nr, tpgid = self._stat(info["pid"])
        seen[key] = tracked
        memory = info["memory_info"]
        return ProcInfo(
            pid=info["pid"],
            ppid=info["ppid"] or 0,
            tty=ttys.get(tty_nr) if tty_nr else None,
            pgid=pgid,
            tpgid=tpgid,
            name=info["name"] or "?",
            cmdline=tuple(info["cmdline"] or ()),
            create_time=info["create_time"],
            cpu_pct=cpu if known else None,
            rss=memory.rss if memory else 0,
        )

    def snapshot(self) -> list[ProcInfo]:
        seen: dict[tuple[int, float], psutil.Process] = {}
        infos: list[ProcInfo] = []
        ttys = self._terminal_map()
        attrs = ["pid", "ppid", "name", "cmdline", "create_time", "memory_info"]
        for proc in psutil.process_iter(attrs):
            try:
                infos.append(self._read(proc, ttys, seen))
            except (psutil.Error, OSError, ValueError):
                continue
        self._seen = seen
        return infos


@dataclass(frozen=True)
class PaneProcs:
    fg_name: str | None
    fg_pid: int | None
    claude_pid: int | None
    cpu_pct: float | None
    rss: int
    n_procs: int
    mcp_cpu_pct: float
    mcp_rss: int
    docker_build: bool = False  # a `docker build` client runs in the pane


MCP_PREFIXES = (("npm", "exec"), ("npx",), ("uvx",))


def _words(p: ProcInfo) -> list[str]:
    # Processes like `npm exec` rewrite argv into one string, so re-split it.
    words = " ".join(p.cmdline).split()
    if words:
        words[0] = Path(words[0]).name
    return words


def is_claude(p: ProcInfo) -> bool:
    words = _words(p)
    if not words:
        return p.name == "claude"
    return words[0] == "claude" or (words[0] == "node" and len(words) > 1 and "claude" in words[1])


def is_mcp_server(p: ProcInfo) -> bool:
    words = _words(p)
    if any(tuple(words[: len(prefix)]) == prefix for prefix in MCP_PREFIXES):
        return True
    return "mcp" in " ".join(p.cmdline).lower()


_BUILD_GROUPS = frozenset({"buildx", "image", "builder"})
# compose subcommands, to find which one a `docker compose -f x.yml …` line runs
_COMPOSE_COMMANDS = frozenset(
    {"build", "up", "down", "run", "exec", "start", "stop", "restart", "create", "rm", "kill",
     "logs", "ps", "pull", "push", "config", "watch"}
)  # fmt: skip


def _compose_builds(words: list[str]) -> bool:
    return next((w for w in words if w in _COMPOSE_COMMANDS), None) == "build"


def is_docker_build(p: ProcInfo) -> bool:
    """A docker client that hands a build to the daemon: the build itself runs outside the pane."""
    words = _words(p)
    if not words:
        return False
    if words[0] == "docker-compose":
        return _compose_builds(words[1:])
    if words[0] != "docker":
        return False
    rest = [w for w in words[1:] if not w.startswith("-")]
    if not rest:
        return False
    if rest[0] == "build":
        return True
    if rest[0] in _BUILD_GROUPS:
        return rest[1:2] == ["build"]
    return rest[0] == "compose" and _compose_builds(rest[1:])


def _subtree(start: Iterable[ProcInfo], children: dict[int, list[ProcInfo]]) -> list[ProcInfo]:
    out: list[ProcInfo] = []
    seen: set[int] = set()
    stack = list(start)
    while stack:
        p = stack.pop()
        if p.pid in seen:
            continue
        seen.add(p.pid)
        out.append(p)
        stack.extend(children.get(p.pid, ()))
    return out


def _foreground(on_tty: Sequence[ProcInfo]) -> ProcInfo | None:
    group = [p for p in on_tty if p.tpgid > 0 and p.pgid == p.tpgid]
    if group:
        leader = next((p for p in group if p.pid == p.pgid), None)
        return leader or max(group, key=lambda p: p.create_time)
    return max(on_tty, key=lambda p: p.create_time, default=None)


def _split_mcp(
    tree: list[ProcInfo], children: dict[int, list[ProcInfo]]
) -> tuple[list[ProcInfo], list[ProcInfo]]:
    """(work, mcp): MCP servers are direct children of a claude process, with their subtrees."""
    roots = [c for p in tree if is_claude(p) for c in children.get(p.pid, ()) if is_mcp_server(c)]
    mcp_pids = {p.pid for p in _subtree(roots, children)}
    return [p for p in tree if p.pid not in mcp_pids], [p for p in tree if p.pid in mcp_pids]


def _total_cpu(procs: Sequence[ProcInfo]) -> float | None:
    """None when no process has a CPU sample yet (all first-seen)."""
    values = [p.cpu_pct for p in procs]
    return None if all(c is None for c in values) else sum(c or 0.0 for c in values)


def _pane_procs(on_tty: list[ProcInfo], children: dict[int, list[ProcInfo]]) -> PaneProcs:
    work, mcp = _split_mcp(_subtree(on_tty, children), children)
    fg = _foreground(on_tty)
    claude = next((p for p in work if is_claude(p)), None)
    return PaneProcs(
        fg_name=fg.name if fg else None,
        fg_pid=fg.pid if fg else None,
        claude_pid=claude.pid if claude else None,
        cpu_pct=_total_cpu(work),
        rss=sum(p.rss for p in work),
        n_procs=len(work),
        mcp_cpu_pct=sum(p.cpu_pct or 0.0 for p in mcp),
        mcp_rss=sum(p.rss for p in mcp),
        docker_build=any(is_docker_build(p) for p in work),
    )


def summarize(procs: Sequence[ProcInfo], ttys: Iterable[str]) -> dict[str, PaneProcs]:
    children: dict[int, list[ProcInfo]] = defaultdict(list)
    by_tty: dict[str, list[ProcInfo]] = defaultdict(list)
    for p in procs:
        if p.ppid != p.pid:
            children[p.ppid].append(p)
        if p.tty:
            by_tty[p.tty].append(p)
    return {tty: _pane_procs(by_tty.get(tty, []), children) for tty in ttys}


class ProcessSampler:
    def __init__(self, table: ProcessTable | None = None) -> None:
        self._table = table or PsutilTable()

    def sample(self, ttys: Iterable[str]) -> dict[str, PaneProcs]:
        return summarize(self._table.snapshot(), ttys)
