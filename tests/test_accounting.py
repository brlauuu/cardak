from pathlib import Path

import pytest

from cardak.collectors.docker import DockerWork
from cardak.collectors.machine import MachineSample
from cardak.core.accounting import account, match_project
from cardak.core.resolver import OTHER, Resolved
from cardak.core.snapshot import DeckSnapshot, DockerLine, Group, PaneRow, group_for

GIB = 2**30
R = Path("/home/user/repos")
WEBSHOP, API = R / "webshop", R / "api-gw"
ROOTS = [WEBSHOP, API, API / "vendor" / "lib"]


def c(
    name: str = "x", compose: str | None = None, mounts: tuple[str, ...] = (), rss: int = 0
) -> DockerWork:
    return DockerWork(
        "container",
        name,
        name,
        rss,
        None,
        Path(compose) if compose else None,
        tuple(Path(m) for m in mounts),
    )


def build(rss: int, cpu: float | None = 10.0) -> DockerWork:
    return DockerWork("build", "b", "build", rss, cpu)


def row(pane_id: int, rss: int = GIB, cpu: float = 10.0, building: bool = False) -> PaneRow:
    return PaneRow(
        pane_id=pane_id,
        label="zsh",
        detail=None,
        cwd=None,
        cwd_display=".",
        cpu_pct=cpu,
        rss=rss,
        is_self=False,
        docker_build=building,
    )


def group(kind: str, key: Path | None, *rows: PaneRow) -> Group:
    return Group(
        kind=kind,  # type: ignore[arg-type]
        key=key,
        name=key.name if key else "Other",
        accent=None,
        path=None,
        cpu_pct=sum(r.cpu_pct or 0.0 for r in rows),
        rss=sum(r.rss for r in rows),
        panes=rows,
    )


def make_group(root: Path) -> Group:
    return group_for(Resolved("config", root, root.name), Path("/home/user"))


def machine(used_gib: float, cpu_pct: float | None = 25.0) -> MachineSample:
    return MachineSample(32 * GIB, int((32 - used_gib) * GIB), 0, 0, cpu_pct, 16)


def run(
    groups: list[Group], work: list[DockerWork], m: MachineSample | None = None
) -> DeckSnapshot:
    return account(DeckSnapshot(groups=tuple(groups)), work, m, ROOTS, make_group)


def by_key(snap: DeckSnapshot) -> dict[Path | None, Group]:
    return {g.key: g for g in snap.groups}


def test_mount_matching() -> None:
    assert match_project(c(compose="/home/user/repos/webshop"), ROOTS) == WEBSHOP
    assert match_project(c(compose="/home/user/repos/webshop/deploy"), ROOTS) == WEBSHOP
    assert match_project(c(mounts=("/home/user/repos/api-gw/data",)), ROOTS) == API
    assert match_project(c(mounts=("/home/user/repos/api-gw/vendor/lib/x",)), ROOTS) == ROOTS[2]
    assert match_project(c(mounts=("/home/user",)), ROOTS) is None
    assert match_project(c(mounts=("/tmp", "/home/user/repos/api-gw")), ROOTS) == API
    assert match_project(c(), ROOTS) is None


def test_container_adds_to_its_project() -> None:
    work = [DockerWork("container", "i", "webshop-db-1", 2 * GIB, 30.0, WEBSHOP)]
    snap = run([group("config", WEBSHOP, row(1))], work)
    webshop = by_key(snap)[WEBSHOP]
    assert webshop.rss == 3 * GIB and webshop.cpu_pct == 40.0
    assert webshop.docker == (DockerLine("docker: webshop-db-1", 2 * GIB, 30.0),)


def test_project_with_only_docker_gets_a_group() -> None:
    groups = [group("config", WEBSHOP, row(1)), group("other", None, row(2))]
    snap = run(groups, [c("cache", mounts=("/home/user/repos/api-gw/data",), rss=GIB)])
    assert [g.key for g in snap.groups] == [WEBSHOP, API, None]
    assert by_key(snap)[API].rss == GIB and by_key(snap)[API].panes == ()


def test_one_building_project_owns_the_builds() -> None:
    groups = [group("config", WEBSHOP, row(1, building=True), row(3, building=True))]
    snap = run(groups, [build(GIB, 10.0), build(GIB // 2, None)])
    assert by_key(snap)[WEBSHOP].docker == (DockerLine("docker build", GIB + GIB // 2, 10.0),)
    assert by_key(snap)[WEBSHOP].rss == 2 * GIB + GIB + GIB // 2


def test_builds_in_two_projects_go_to_other() -> None:
    groups = [
        group("config", WEBSHOP, row(1, building=True)),
        group("config", API, row(2, building=True)),
    ]
    snap = run(groups, [build(GIB)])
    assert by_key(snap)[None].docker == (DockerLine("docker builds, 2 panes", GIB, 10.0),)
    assert by_key(snap)[WEBSHOP].rss == GIB and by_key(snap)[WEBSHOP].docker == ()


def test_builds_with_no_building_pane_go_to_other() -> None:
    snap = run([group("config", WEBSHOP, row(1))], [build(GIB)])
    assert by_key(snap)[None].docker == (DockerLine("docker build", GIB, 10.0),)


def test_unmatched_container_is_listed_under_other_without_adding() -> None:
    snap = run([group("other", None, row(2))], [c("scratch", rss=5 * GIB)])
    other = by_key(snap)[None]
    assert other.docker == (DockerLine("docker: scratch", 5 * GIB, None),)
    assert other.rss == GIB


def test_other_is_the_remainder() -> None:
    groups = [
        group("config", WEBSHOP, row(1, rss=6 * GIB, cpu=150.0)),
        group("other", None, row(2, rss=GIB, cpu=50.0)),
    ]
    other = by_key(run(groups, [], machine(20, cpu_pct=25.0)))[None]
    assert other.rss == 14 * GIB and other.outside_rss == 13 * GIB
    assert other.cpu_pct == pytest.approx(250.0)
    assert other.outside_cpu_pct == pytest.approx(200.0)


def test_other_clamped_at_zero() -> None:
    groups = [group("config", WEBSHOP, row(1, rss=6 * GIB)), group("other", None, row(2))]
    other = by_key(run(groups, [], machine(4, cpu_pct=None)))[None]
    assert other.rss == 0 and other.outside_rss == 0
    assert other.cpu_pct == 0.0 and other.outside_cpu_pct == 0.0


def test_other_counts_mcp_of_its_panes() -> None:
    mcp = PaneRow(
        pane_id=2, label="claude", detail=None, cwd=None, cwd_display="~", cpu_pct=0.0,
        rss=GIB, is_self=False, mcp_rss=GIB,
    )  # fmt: skip
    other = by_key(run([group("other", None, mcp)], [], machine(10)))[None]
    assert other.outside_rss == 8 * GIB


def test_other_appears_without_unassigned_panes() -> None:
    snap = run([group("config", WEBSHOP, row(1))], [], machine(10))
    last = snap.groups[-1]
    assert last.kind == "other" and last.panes == () and last.rss == 9 * GIB
    assert last.name == OTHER.name


def test_no_machine_leaves_other_alone() -> None:
    snap = run([group("config", WEBSHOP, row(1)), group("other", None, row(2))], [])
    other = by_key(snap)[None]
    assert other.rss == GIB and other.outside_rss is None
    assert snap.machine is None


def test_waiting_group_is_not_a_project() -> None:
    waiting = group("waiting", None, row(1))
    snap = run([waiting, group("config", WEBSHOP, row(1))], [], machine(10))
    assert snap.groups[0] == waiting
    assert by_key(snap)[None].rss == 9 * GIB  # used 10 − webshop 1; the waiting copy not counted
