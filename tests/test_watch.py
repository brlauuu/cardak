from dataclasses import replace
from pathlib import Path

from cardak.collectors.machine import MachineSample
from cardak.core.config import ResourceSettings
from cardak.core.resources import derive
from cardak.core.snapshot import DeckSnapshot, DockerLine, Group, PaneRow
from cardak.core.watch import ResourceAlert, Watcher, gb

GIB = 2**30
T = derive(ResourceSettings(), 32 * GIB, 16)
WEBSHOP = Path("/home/user/repos/webshop")
API = Path("/home/user/repos/api-gw")


def row(pane_id: int, label: str = "zsh", rss: float = 0.1, cpu: float | None = 1.0) -> PaneRow:
    return PaneRow(
        pane_id=pane_id,
        label=label,
        detail=None,
        cwd=None,
        cwd_display=".",
        cpu_pct=cpu,
        rss=int(rss * GIB),
        is_self=False,
    )


def group(kind: str, key: Path | None, name: str, *rows: PaneRow, **kw: object) -> Group:
    g = Group(
        kind=kind,  # type: ignore[arg-type]
        key=key,
        name=name,
        accent=None,
        path=None,
        cpu_pct=sum(r.cpu_pct or 0.0 for r in rows),
        rss=sum(r.rss for r in rows),
        panes=rows,
    )
    return replace(g, **kw)  # type: ignore[arg-type]


def machine(used: float = 16.0, cpu: float | None = 20.0, swap_free: int = GIB) -> MachineSample:
    return MachineSample(32 * GIB, int((32 - used) * GIB), GIB, swap_free, cpu, 16)


def snap(*groups: Group, m: MachineSample | None = None) -> DeckSnapshot:
    return DeckSnapshot(groups=groups, machine=m or machine())


def webshop(rss: float = 8.5, extra: float = 0.5, cpu: float = 5.0) -> Group:
    return group(
        "config", WEBSHOP, "webshop", row(12, "pytest", rss, cpu), row(13, "zsh", extra, 0.0)
    )


def run(w: Watcher, s: DeckSnapshot, *times: float) -> list[ResourceAlert]:
    out: list[ResourceAlert] = []
    for now in times:
        out += w.step(s, T, now)
    return out


def test_gb() -> None:
    assert gb(int(13.9 * GIB)) == "13.9 GB"


def test_big_project_notifies_once_then_logs_quietly() -> None:
    w = Watcher()
    [first] = w.step(snap(webshop()), T, 0.0)
    assert (first.kind, first.level, first.subject, first.notify) == (
        "big", "warning", f"project:{WEBSHOP}", True,
    )  # fmt: skip
    assert (
        first.text == "webshop · pytest in pane 12 · 8.5 GB of the project's 9.0 GB · RAM 50% used"
    )
    assert first.title == "cardak · webshop is big"
    assert w.step(snap(webshop()), T, 2.0) == []
    [quiet] = w.step(snap(webshop()), T, 61.0)
    assert not quiet.notify
    [again] = w.step(snap(webshop()), T, 601.0)
    assert again.notify


def test_flapping_notifies_once() -> None:
    w = Watcher()
    alerts = []
    for i in range(150):  # 5 min at 2 s, alternating over and under
        alerts += w.step(snap(webshop(rss=8.5 if i % 2 == 0 else 6.0)), T, i * 2.0)
    assert sum(a.notify for a in alerts) == 1


def test_memory_warning_needs_10_seconds_critical_does_not() -> None:
    w = Watcher()
    tight = snap(webshop(rss=1.0), m=machine(used=28.0))
    assert [a for a in run(w, tight, 0.0, 5.0) if a.kind == "memory"] == []
    [warn] = [a for a in w.step(tight, T, 10.0) if a.kind == "memory"]
    assert (warn.level, warn.notify, warn.subject) == ("warning", True, "machine")
    assert warn.title == "cardak · memory tight"
    [crit] = [a for a in w.step(snap(webshop(rss=1.0), m=machine(used=30.0)), T, 12.0)
              if a.kind == "memory"]  # fmt: skip
    assert (crit.level, crit.notify, crit.title) == ("critical", True, "cardak · memory critical")


def test_memory_text_names_the_culprit_or_outside() -> None:
    api = group("config", API, "api-gw", row(3, "pytest", 6.0), row(4, "zsh", 0.5))
    small = group("config", WEBSHOP, "webshop", row(1, "zsh", 1.0))
    w = Watcher()
    [a] = w.step(snap(small, api, m=machine(used=30.0)), T, 0.0)
    assert a.text == (
        "2.0 GB free of 32.0 GB (6% free) · biggest: api-gw · pytest in pane 3 · 6.0 GB of the "
        "project's 6.5 GB"
    )
    other = group("other", None, "Other", outside_rss=20 * GIB, rss=20 * GIB)
    [b] = Watcher().step(snap(api, other, m=machine(used=30.0)), T, 0.0)
    assert b.text.endswith("biggest: most of it is outside WezTerm")
    [c] = Watcher().step(snap(api, m=machine(used=20.0, swap_free=0)), T, 0.0)
    assert c.level == "critical" and "(38% free), swap full · biggest: api-gw" in c.text


def test_memory_culprit_can_be_unattributed_docker_builds() -> None:
    builds = DockerLine("docker builds, 2 panes", 9 * GIB, 50.0)
    other = group("other", None, "Other", docker=(builds,), rss=9 * GIB, outside_rss=0)
    [a] = Watcher().step(snap(webshop(rss=1.0), other, m=machine(used=30.0)), T, 0.0)
    assert a.text.endswith("biggest: docker builds, 2 panes · 9.0 GB")


def test_growth_over_the_sustain_window() -> None:
    w = Watcher()

    def at(now: float) -> DeckSnapshot:
        pane = row(7, "python", 1.0 + 1.2 * now / 60)
        return snap(group("other", None, "Other", pane))

    alerts = [a for now in range(0, 121, 2) for a in w.step(at(float(now)), T, float(now))]
    growth = [a for a in alerts if a.kind == "growth"]
    assert len(growth) == 1
    assert growth[0].subject == "pane:7" and growth[0].text == (
        "pane 7 · python · 3.4 GB, +1.2 GB/min · RAM 50% used"
    )


def test_growth_needs_the_whole_window() -> None:
    w = Watcher()
    alerts = []
    for now in range(0, 61, 2):
        pane = row(7, "python", 1.0 + 3.0 * now / 60)
        alerts += w.step(snap(group("other", None, "Other", pane)), T, float(now))
    assert [a for a in alerts if a.kind == "growth"] == []


def test_busy_needs_sustain() -> None:
    w = Watcher()
    busy = snap(webshop(rss=1.0, extra=0.1, cpu=900.0))
    assert [a for a in run(w, busy, *range(0, 119, 2)) if a.kind == "busy"] == []
    [a] = [a for a in w.step(busy, T, 120.0) if a.kind == "busy"]
    assert a.text == "webshop · pytest in pane 12 · 900% CPU of the project's 900% · RAM 50% used"
    assert a.title == "cardak · webshop is busy"


def test_machine_busy() -> None:
    w = Watcher()
    hot = snap(webshop(rss=1.0, cpu=600.0), m=machine(cpu=95.0))
    assert [a for a in run(w, hot, 0.0, 60.0) if a.kind == "machine_busy"] == []
    [a] = [a for a in w.step(hot, T, 120.0) if a.kind == "machine_busy"]
    assert a.subject == "machine" and a.title == "cardak · machine busy"
    assert a.text == "CPU 95% for 2 min · busiest: webshop · pytest in pane 12 · 600% CPU"
    w2 = Watcher()
    run(w2, hot, 0.0, 60.0)
    w2.step(snap(webshop(rss=1.0), m=machine(cpu=None)), T, 62.0)
    assert [a for a in w2.step(hot, T, 120.0) if a.kind == "machine_busy"] == []


def test_other_is_never_a_culprit_of_big() -> None:
    other = group("other", None, "Other", row(5, "zsh", 0.5), rss=20 * GIB, outside_rss=19 * GIB)
    alerts = Watcher().step(snap(other), T, 0.0)
    assert alerts == []


def test_one_alert_when_a_pane_pushes_its_project_over() -> None:
    alerts = Watcher().step(snap(group("config", WEBSHOP, "webshop", row(12, "pytest", 9.0))), T, 0)
    assert [(a.kind, a.subject) for a in alerts] == [("big", f"project:{WEBSHOP}")]


def test_pane_in_no_project_counts_its_mcp() -> None:
    pane = replace(row(9, "claude", 5.0), mcp_rss=4 * GIB)
    [a] = Watcher().step(snap(group("other", None, "Other", pane)), T, 0.0)
    assert a.subject == "pane:9" and a.text == "pane 9 · claude · 9.0 GB · RAM 50% used"


def test_closed_pane_forgets_its_timers() -> None:
    w = Watcher()
    busy = snap(group("other", None, "Other", row(7, "cargo", 0.5, 900.0)))
    gone = snap(group("other", None, "Other"))
    run(w, busy, 0.0, 30.0, 58.0)
    w.step(gone, T, 60.0)
    assert [a for a in run(w, busy, 62.0, 122.0) if a.kind == "busy"] == []
    assert [a.kind for a in w.step(busy, T, 182.0)] == ["busy"]


def test_waiting_group_is_ignored() -> None:
    big = row(12, "pytest", 9.0)
    waiting = group("waiting", None, "Waiting on you", big)
    alerts = Watcher().step(snap(waiting, group("config", WEBSHOP, "webshop", big)), T, 0.0)
    assert [a.subject for a in alerts] == [f"project:{WEBSHOP}"]


def test_docker_line_can_be_the_biggest_piece() -> None:
    g = replace(
        group("config", WEBSHOP, "webshop", row(1, "zsh", 0.5)),
        docker=(DockerLine("docker: webshop-db-1", 8 * GIB, 10.0),),
        rss=int(8.5 * GIB),
    )
    [a] = Watcher().step(snap(g), T, 0.0)
    assert a.text.startswith("webshop · docker: webshop-db-1 · 8.0 GB of the project's 8.5 GB")


def test_no_machine_no_alerts() -> None:
    assert Watcher().step(DeckSnapshot(groups=(webshop(),)), T, 0.0) == []
