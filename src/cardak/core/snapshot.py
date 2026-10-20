"""Turn collector output into the grouped view the deck renders. Pure: no I/O."""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from cardak.collectors.claude import ClaudeSession
from cardak.collectors.processes import PaneProcs
from cardak.collectors.wezterm import Pane
from cardak.core.resolver import Kind, ProjectResolver, Resolved

ClaudeLookup = Callable[[int], ClaudeSession | None]
AccentFor = Callable[[Resolved], str | None]


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


@dataclass(frozen=True)
class Group:
    kind: Kind
    key: Path | None
    name: str
    accent: str | None
    path: str | None
    cpu_pct: float
    rss: int
    panes: tuple[PaneRow, ...]
    theme: str | None = None


@dataclass(frozen=True)
class DeckSnapshot:
    groups: tuple[Group, ...] = ()
    n_panes: int = 0
    error: str | None = None
    config_error: str | None = None
    config_warnings: tuple[str, ...] = ()

    @property
    def n_projects(self) -> int:
        return sum(1 for g in self.groups if g.kind != "other")


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


def _row(pane: Pane, root: Path | None, ctx: _Context) -> PaneRow:
    pp = ctx.procs.get(pane.tty) if pane.tty else None
    label = _label(pp, ctx.claude_lookup)
    return PaneRow(
        pane_id=pane.pane_id,
        label=label,
        detail=_detail(pane.title, label),
        cwd=pane.cwd,
        cwd_display=_cwd_display(pane.cwd, root, ctx.home),
        cpu_pct=pp.cpu_pct if pp else None,
        rss=pp.rss if pp else 0,
        is_self=pane.pane_id == ctx.self_pane_id,
    )


def _group(resolved: Resolved, members: list[Pane], ctx: _Context) -> Group:
    rows = tuple(_row(pane, resolved.key, ctx) for pane in members)
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
        cpu_pct=sum(r.cpu_pct or 0.0 for r in rows),
        rss=sum(r.rss for r in rows),
        panes=rows,
        theme=resolved.theme,
    )


def build_snapshot(
    panes: Iterable[Pane],
    procs: Mapping[str, PaneProcs],
    resolver: ProjectResolver,
    claude_lookup: ClaudeLookup,
    self_pane_id: int | None,
    home: Path,
    accent_for: AccentFor | None = None,
) -> DeckSnapshot:
    buckets: dict[Resolved, list[Pane]] = {}
    for pane in sorted(panes, key=lambda p: (p.window_id, p.tab_id, p.pane_id)):
        buckets.setdefault(resolver.resolve(pane.cwd), []).append(pane)
    ctx = _Context(procs, claude_lookup, self_pane_id, home, accent_for)
    ranked = sorted(buckets.items(), key=lambda item: _rank(item[0]))
    groups = tuple(_group(resolved, members, ctx) for resolved, members in ranked)
    return DeckSnapshot(groups=groups, n_panes=sum(len(g.panes) for g in groups))
