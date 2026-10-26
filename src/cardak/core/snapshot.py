"""Turn collector output into the grouped view the deck renders. Pure: no I/O."""

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from cardak.collectors.claude import ClaudeSession
from cardak.collectors.machine import MachineSample
from cardak.collectors.processes import PaneProcs
from cardak.collectors.wezterm import Pane
from cardak.core.agents import SessionState, Status, WaitKind
from cardak.core.resolver import Kind, ProjectResolver, Resolved
from cardak.core.resources import MemoryLevel, Thresholds

ClaudeLookup = Callable[[int], ClaudeSession | None]
AccentFor = Callable[[Resolved], str | None]
GroupKind = Kind | Literal["waiting"]
WAITING_NAME = "Waiting on you"


@dataclass(frozen=True)
class AgentView:
    session_id: str
    status: Status
    kind: WaitKind | None
    since: float
    reason: str | None
    current_tool: str | None
    project: str
    summary: str | None = None  # the current wait's summary, when one was written


@dataclass(frozen=True)
class PaneRow:
    pane_id: int
    label: str
    detail: str | None
    cwd: Path | None
    cwd_display: str
    cpu_pct: float | None
    rss: int
    is_self: bool
    is_claude: bool = False
    agent: AgentView | None = None  # None: not Claude, or no hook record for its session
    mcp_rss: int = 0  # MCP servers under its claude; rss/cpu_pct above are the work only
    mcp_cpu_pct: float = 0.0
    docker_build: bool = False
    ram_spark: str = ""
    over: bool = False  # over a pane threshold right now
    pids: frozenset[int] = frozenset()


@dataclass(frozen=True)
class DockerLine:
    label: str  # "docker: webshop-db-1", "docker build", "docker builds, 2 panes"
    rss: int
    cpu_pct: float | None


@dataclass(frozen=True)
class Group:
    kind: GroupKind
    key: Path | None
    name: str
    accent: str | None
    path: str | None
    cpu_pct: float
    rss: int
    panes: tuple[PaneRow, ...]
    theme: str | None = None
    docker: tuple[DockerLine, ...] = ()
    outside_rss: int | None = None  # Other only: the remainder minus its panes
    outside_cpu_pct: float | None = None
    ram_spark: str = ""
    over: bool = False


@dataclass(frozen=True)
class MachineView:
    sample: MachineSample
    level: MemoryLevel | None
    ram_spark: str
    cpu_spark: str


@dataclass(frozen=True)
class DeckSnapshot:
    groups: tuple[Group, ...] = ()
    n_panes: int = 0
    error: str | None = None
    internal: bool = False  # error is a bug in cardak, not WezTerm being unreachable
    config_error: str | None = None
    config_warnings: tuple[str, ...] = ()
    n_waiting: int = 0
    summary_capped: bool = False  # this month's summaries reached [summary] monthly_limit_usd
    summary_limit: float = 0.0
    machine: MachineSample | None = None
    machine_view: MachineView | None = None
    thresholds: Thresholds | None = None  # set whenever there is a machine sample

    @property
    def n_projects(self) -> int:
        return sum(1 for g in self.groups if g.kind not in ("other", "waiting"))


def tilde(path: Path, home: Path) -> str:
    if path == home:
        return "~"
    return f"~/{path.relative_to(home)}" if path.is_relative_to(home) else str(path)


def _rank(r: Resolved) -> tuple[int, int, str]:
    if r.kind == "config":
        return (0, r.order, "")
    if r.kind == "git":
        return (1, 0, r.name.lower())
    return (2, 0, "")


def _label(pp: PaneProcs | None, claude_lookup: ClaudeLookup) -> str:
    if pp is None:
        return "?"
    if pp.claude_pid is not None:
        session = claude_lookup(pp.claude_pid)
        if session is None:
            return "claude (new)"
        return f"claude · {session.name}" if session.name else "claude"
    return pp.fg_name or "?"


def _detail(title: str, label: str) -> str | None:
    if not title or title == label or title.startswith(("~", "/", "..")):
        return None  # path-like titles repeat the cwd column
    return title


def _cwd_display(cwd: Path | None, root: Path | None, home: Path) -> str:
    if cwd is None:
        return "?"
    if root is not None and cwd.is_relative_to(root):
        rel = cwd.relative_to(root)
        return "." if rel == Path(".") else str(rel)
    return tilde(cwd, home)


@dataclass(frozen=True)
class _Context:
    procs: Mapping[str, PaneProcs]
    claude_lookup: ClaudeLookup
    self_pane_id: int | None
    home: Path
    accent_for: AccentFor | None = None
    by_pane: Mapping[int, SessionState] | None = None
    by_session: Mapping[str, SessionState] | None = None
    summaries: Mapping[str, str] | None = None


def _session_for(pane_id: int, claude_pid: int, ctx: _Context) -> SessionState | None:
    if ctx.by_pane and pane_id in ctx.by_pane:
        return ctx.by_pane[pane_id]
    meta = ctx.claude_lookup(claude_pid)  # fallback: the pid's ~/.claude/sessions record
    return ctx.by_session.get(meta.session_id) if meta and ctx.by_session else None


def _agent(pp: PaneProcs | None, pane_id: int, project: str, ctx: _Context) -> AgentView | None:
    if pp is None or pp.claude_pid is None:
        return None
    state = _session_for(pane_id, pp.claude_pid, ctx)
    if state is None:
        return None
    return AgentView(
        session_id=state.session_id,
        status=state.status,
        kind=state.kind,
        since=state.status_since,
        reason=state.reason,
        current_tool=state.current_tool,
        project=project,
        summary=(ctx.summaries or {}).get(state.session_id) if state.status == "waiting" else None,
    )


def _row(pane: Pane, resolved: Resolved, ctx: _Context) -> PaneRow:
    pp = ctx.procs.get(pane.tty) if pane.tty else None
    label = _label(pp, ctx.claude_lookup)
    return PaneRow(
        pane_id=pane.pane_id,
        label=label,
        detail=_detail(pane.title, label),
        cwd=pane.cwd,
        cwd_display=_cwd_display(pane.cwd, resolved.key, ctx.home),
        cpu_pct=pp.cpu_pct if pp else None,
        rss=pp.rss if pp else 0,
        is_self=pane.pane_id == ctx.self_pane_id,
        is_claude=pp is not None and pp.claude_pid is not None,
        agent=_agent(pp, pane.pane_id, resolved.name, ctx),
        mcp_rss=pp.mcp_rss if pp else 0,
        mcp_cpu_pct=pp.mcp_cpu_pct if pp else 0.0,
        docker_build=pp.docker_build if pp else False,
        pids=pp.pids if pp else frozenset(),
    )


def _group(resolved: Resolved, members: list[Pane], ctx: _Context) -> Group:
    rows = tuple(_row(pane, resolved, ctx) for pane in members)
    return Group(
        kind=resolved.kind,
        key=resolved.key,
        name=resolved.name,
        accent=(
            ctx.accent_for(resolved)
            if ctx.accent_for and resolved.kind == "config"
            else resolved.accent
        ),
        path=tilde(resolved.key, ctx.home) if resolved.key else None,
        cpu_pct=sum((r.cpu_pct or 0.0) + r.mcp_cpu_pct for r in rows),
        rss=sum(r.rss + r.mcp_rss for r in rows),
        panes=rows,
        theme=resolved.theme,
    )


def group_for(resolved: Resolved, home: Path, accent_for: AccentFor | None = None) -> Group:
    """A group with no panes, for a project that only runs Docker work (or an empty Other)."""
    return _group(resolved, [], _Context({}, lambda _pid: None, None, home, accent_for))


def build_snapshot(
    panes: Iterable[Pane],
    procs: Mapping[str, PaneProcs],
    resolver: ProjectResolver,
    claude_lookup: ClaudeLookup,
    self_pane_id: int | None,
    home: Path,
    accent_for: AccentFor | None = None,
    sessions: Sequence[SessionState] = (),
    summaries: Mapping[str, str] | None = None,
) -> DeckSnapshot:
    buckets: dict[Resolved, list[Pane]] = {}
    for pane in sorted(panes, key=lambda p: (p.window_id, p.tab_id, p.pane_id)):
        buckets.setdefault(resolver.resolve(pane.cwd), []).append(pane)
    by_pane: dict[int, SessionState] = {}
    for s in sorted(sessions, key=lambda s: s.started_at):  # /clear: the newest one wins
        if s.pane_id is not None and s.ended_at is None:
            by_pane[s.pane_id] = s
    by_session = {s.session_id: s for s in sessions if s.ended_at is None}
    ctx = _Context(
        procs, claude_lookup, self_pane_id, home, accent_for, by_pane, by_session, summaries
    )
    ranked = sorted(buckets.items(), key=lambda item: _rank(item[0]))
    groups = tuple(_group(resolved, members, ctx) for resolved, members in ranked)
    n_panes = sum(len(g.panes) for g in groups)
    waiting = _waiting_group(groups)
    if waiting is None:
        return DeckSnapshot(groups=groups, n_panes=n_panes)
    return DeckSnapshot(groups=(waiting, *groups), n_panes=n_panes, n_waiting=len(waiting.panes))


def _waiting_group(groups: Iterable[Group]) -> Group | None:
    rows = [r for g in groups for r in g.panes if r.agent and r.agent.status == "waiting"]
    if not rows:
        return None
    rows.sort(key=lambda r: r.agent.since if r.agent else 0.0)
    return Group(
        kind="waiting",
        key=None,
        name=WAITING_NAME,
        accent=None,
        path=None,
        cpu_pct=0.0,
        rss=0,
        panes=tuple(rows),
    )
