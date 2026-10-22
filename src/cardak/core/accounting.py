"""Attribute Docker work to projects and make Other the remainder of the machine. Pure."""

from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path

from cardak.collectors.docker import DockerWork
from cardak.collectors.machine import MachineSample
from cardak.core.resolver import OTHER
from cardak.core.snapshot import DeckSnapshot, DockerLine, Group, group_for

PROJECT_KINDS = ("config", "git")


def _deepest(path: Path, roots: Sequence[Path]) -> Path | None:
    inside = [r for r in roots if path.is_relative_to(r)]
    return max(inside, key=lambda r: len(r.parts), default=None)


def match_project(work: DockerWork, roots: Sequence[Path]) -> Path | None:
    """The Compose folder's project, else the first bind mount that lies inside a project."""
    if work.compose_dir is not None and (root := _deepest(work.compose_dir, roots)):
        return root
    return next((root for m in work.mounts if (root := _deepest(m, roots))), None)


def _add(g: Group, lines: Sequence[DockerLine], count: bool = True) -> Group:
    if not lines:
        return g
    if not count:
        return replace(g, docker=(*g.docker, *lines))
    return replace(
        g,
        docker=(*g.docker, *lines),
        rss=g.rss + sum(line.rss for line in lines),
        cpu_pct=g.cpu_pct + sum(line.cpu_pct or 0.0 for line in lines),
    )


def _summed(label: str, work: Sequence[DockerWork]) -> DockerLine:
    cpus = [w.cpu_pct for w in work]
    cpu = None if all(c is None for c in cpus) else sum(c or 0.0 for c in cpus)
    return DockerLine(label, sum(w.rss for w in work), cpu)


def _with_other(groups: list[Group]) -> list[Group]:
    if any(g.kind == "other" for g in groups):
        return groups
    return [*groups, group_for(OTHER, Path("/"))]  # Other has no key, so home goes unused


def _by_root(
    work: Sequence[DockerWork], roots: Sequence[Path]
) -> tuple[dict[Path, list[DockerLine]], list[DockerLine]]:
    by_root: dict[Path, list[DockerLine]] = {}
    unmatched: list[DockerLine] = []
    for w in (w for w in work if w.kind == "container"):
        line = DockerLine(f"docker: {w.name}", w.rss, w.cpu_pct)
        root = match_project(w, roots)
        (by_root.setdefault(root, []) if root else unmatched).append(line)
    return by_root, unmatched


def _attach_containers(
    groups: list[Group],
    work: Sequence[DockerWork],
    roots: Sequence[Path],
    make_group: Callable[[Path], Group],
) -> tuple[list[Group], list[DockerLine]]:
    """Groups with their containers added, and the lines no project claimed."""
    by_root, unmatched = _by_root(work, roots)
    present = {g.key for g in groups if g.kind in PROJECT_KINDS}
    new = [make_group(root) for root in by_root if root not in present]
    split = next((i for i, g in enumerate(groups) if g.kind == "other"), len(groups))
    groups = [*groups[:split], *new, *groups[split:]]
    out = [
        _add(g, by_root.get(g.key, [])) if g.key and g.kind in PROJECT_KINDS else g for g in groups
    ]
    return out, unmatched


def _attach_builds(
    groups: list[Group], work: Sequence[DockerWork]
) -> tuple[list[Group], list[DockerLine]]:
    """Builds go to the one project building, else back as a line for Other."""
    builds = [w for w in work if w.kind == "build"]
    if not builds:
        return groups, []
    projects = [g for g in groups if g.kind in PROJECT_KINDS]
    building = [g for g in projects if any(r.docker_build for r in g.panes)]
    if len(building) == 1:
        line = _summed("docker build", builds)
        return [_add(g, [line]) if g is building[0] else g for g in groups], []
    return groups, [_summed(_builds_label(groups), builds)]


def _builds_label(groups: Sequence[Group]) -> str:
    n = sum(1 for g in groups if g.kind != "waiting" for r in g.panes if r.docker_build)
    return f"docker builds, {n} panes" if n > 1 else "docker build"


def _remainder(groups: list[Group], machine: MachineSample) -> list[Group]:
    projects = [g for g in groups if g.kind in PROJECT_KINDS]
    used_cpu = (machine.cpu_pct or 0.0) * machine.n_cpus
    rss = max(0, machine.mem_used - sum(g.rss for g in projects))
    cpu = max(0.0, used_cpu - sum(g.cpu_pct for g in projects))

    def other(g: Group) -> Group:
        pane_rss = sum(r.rss + r.mcp_rss for r in g.panes)
        pane_cpu = sum((r.cpu_pct or 0.0) + r.mcp_cpu_pct for r in g.panes)
        return replace(
            g,
            rss=rss,
            cpu_pct=cpu,
            outside_rss=max(0, rss - pane_rss),
            outside_cpu_pct=max(0.0, cpu - pane_cpu),
        )

    return [other(g) if g.kind == "other" else g for g in groups]


def account(
    snap: DeckSnapshot,
    work: Sequence[DockerWork],
    machine: MachineSample | None,
    roots: Sequence[Path],
    make_group: Callable[[Path], Group],
) -> DeckSnapshot:
    groups, unmatched = _attach_containers(list(snap.groups), work, roots, make_group)
    groups, build_lines = _attach_builds(groups, work)
    if machine is not None or unmatched or build_lines:
        groups = _with_other(groups)
    extra = [*build_lines, *unmatched]  # already inside Other's remainder: listed, not added
    groups = [_add(g, extra, count=False) if g.kind == "other" else g for g in groups]
    if machine is not None:
        groups = _remainder(groups, machine)
    return replace(snap, groups=tuple(groups), machine=machine)
