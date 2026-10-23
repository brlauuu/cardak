"""Per-tty process trees and their CPU/RAM. ProcTable is the only per-process /proc reader."""

import os
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


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


def _read_bytes(path: str) -> bytes:
    fd = os.open(path, os.O_RDONLY)
    try:
        return os.read(fd, 1 << 16)
    finally:
        os.close(fd)


def _argv(raw: bytes) -> tuple[str, ...]:
    """/proc/<pid>/cmdline as psutil splits it: a process that rewrote its title leaves one
    space-joined string (with no trailing NUL, or as a single argument), split on spaces."""
    text = raw.decode(errors="replace")
    parts = [a for a in text.split("\0") if a] if text.endswith("\0") else [text] if text else []
    if len(parts) == 1 and " " in parts[0]:
        parts = [a for a in parts[0].split(" ") if a]
    return tuple(parts)


def _boot_time(proc_root: Path) -> float:
    for line in (proc_root / "stat").read_text().splitlines():
        if line.startswith("btime "):
            return float(line.split()[1])
    raise ValueError("no btime in /proc/stat")


@dataclass(frozen=True)
class _Stat:
    comm: str
    ppid: int
    pgid: int
    tty_nr: int
    tpgid: int
    ticks: int  # utime + stime
    start: int  # starttime, in clock ticks after boot
    rss_pages: int


def _parse_stat(text: str) -> _Stat:
    """/proc/<pid>/stat; comm is in parentheses and may itself contain spaces or ")"."""
    comm = text[text.index("(") + 1 : text.rindex(")")]
    f = text[text.rindex(")") + 2 :].split()
    return _Stat(
        comm,
        int(f[1]),
        int(f[2]),
        int(f[4]),
        int(f[5]),
        int(f[11]) + int(f[12]),
        int(f[19]),
        int(f[21]),
    )


class ProcTable:
    """Reads /proc directly: one stat read per process per snapshot.

    psutil opened several files per process (~140 ms for ~400 processes), too much for a
    daemon polling every 2 s. Command lines are read once a process's argv has settled
    (`npm exec` rewrites its argv shortly after start), and again after an exec (new comm).
    """

    SETTLE_S = 30.0

    def __init__(
        self,
        proc_root: Path = Path("/proc"),
        dev_root: Path = Path("/dev"),
        clock: Callable[[], float] = time.monotonic,
        clk_tck: int | None = None,
        page_size: int | None = None,
        wall: Callable[[], float] = time.time,
    ) -> None:
        self._proc_root = proc_root
        self._root = str(proc_root)  # plain strings: pathlib costs more than the reads here
        self._dev_root = dev_root
        self._clock = clock
        self._wall = wall
        self._clk_tck = clk_tck or os.sysconf("SC_CLK_TCK")
        self._page_size = page_size or os.sysconf("SC_PAGE_SIZE")
        self._boot: float | None = None
        self._ticks: dict[tuple[int, int], tuple[int, float]] = {}  # (pid, start) → (ticks, when)
        self._cmdlines: dict[tuple[int, int, str], tuple[str, ...]] = {}

    def _terminal_map(self) -> dict[int, str]:
        """Device number -> tty path, rebuilt per snapshot (a pane opened later has a new pts)."""
        ttys: dict[int, str] = {}
        for path in [*self._dev_root.glob("tty*"), *self._dev_root.glob("pts/*")]:
            try:
                ttys[path.stat().st_rdev] = str(path)
            except OSError:
                continue
        return ttys

    def _cmdline(self, pid: int, st: _Stat, created: float) -> tuple[str, ...]:
        key = (pid, st.start, st.comm)
        cached = self._cmdlines.get(key)
        if cached is not None and self._wall() - created >= self.SETTLE_S:
            return cached
        argv = _argv(_read_bytes(f"{self._root}/{pid}/cmdline"))
        self._cmdlines[key] = argv
        return argv

    def _read(
        self,
        pid: int,
        ttys: dict[int, str],
        now: float,
        ticks: dict[tuple[int, int], tuple[int, float]],
    ) -> ProcInfo:
        st = _parse_stat(_read_bytes(f"{self._root}/{pid}/stat").decode(errors="replace"))
        created = (self._boot or 0.0) + st.start / self._clk_tck
        argv = self._cmdline(pid, st, created)
        name = st.comm
        if len(name) == 15 and argv and Path(argv[0]).name.startswith(name):
            name = Path(argv[0]).name  # comm is cut at 15 characters
        key = (pid, st.start)
        prev = self._ticks.get(key)
        ticks[key] = (st.ticks, now)
        cpu = None
        if prev is not None and now > prev[1]:
            cpu = (st.ticks - prev[0]) / self._clk_tck / (now - prev[1]) * 100
        return ProcInfo(
            pid=pid,
            ppid=st.ppid,
            tty=ttys.get(st.tty_nr) if st.tty_nr else None,
            pgid=st.pgid,
            tpgid=st.tpgid,
            name=name,
            cmdline=argv,
            create_time=created,
            cpu_pct=cpu,
            rss=st.rss_pages * self._page_size,
        )

    def snapshot(self) -> list[ProcInfo]:
        if self._boot is None:
            self._boot = _boot_time(self._proc_root)
        ttys = self._terminal_map()
        now = self._clock()
        ticks: dict[tuple[int, int], tuple[int, float]] = {}
        infos: list[ProcInfo] = []
        for entry in os.listdir(self._root):
            if not entry.isdigit():
                continue
            try:
                infos.append(self._read(int(entry), ttys, now, ticks))
            except (OSError, ValueError, IndexError):
                continue  # exited, or unreadable
        self._ticks = ticks
        alive = {(i.pid, round((i.create_time - self._boot) * self._clk_tck)) for i in infos}
        self._cmdlines = {k: v for k, v in self._cmdlines.items() if k[:2] in alive}
        return sorted(infos, key=lambda i: i.pid)


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
    {
        "build",
        "up",
        "down",
        "run",
        "exec",
        "start",
        "stop",
        "restart",
        "create",
        "rm",
        "kill",
        "logs",
        "ps",
        "pull",
        "push",
        "config",
        "watch",
    }
)


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
        self._table = table or ProcTable()

    def sample(self, ttys: Iterable[str]) -> dict[str, PaneProcs]:
        return summarize(self._table.snapshot(), ttys)
