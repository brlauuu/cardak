from dataclasses import replace
from datetime import date
from pathlib import Path

from cardak.collectors.machine import MachineSample
from cardak.core.config import ResourceSettings
from cardak.core.history import (
    CELLS,
    HistoryRow,
    Rollup,
    band,
    edges,
    history_rows,
    legend,
    sort_rows,
)
from cardak.core.resources import derive
from cardak.core.snapshot import DeckSnapshot, Group
from cardak.core.store import DayPeak, MinuteRow

GIB = 2**30
WEB = Path("/home/user/repos/webshop")
API = Path("/home/user/repos/api-gw")
MACHINE = MachineSample(32 * GIB, 16 * GIB, GIB, GIB, 20.0, 16)
TODAY = date(2026, 10, 22)


def grp(kind: str, key: Path | None, name: str, rss: int, cpu: float) -> Group:
    return Group(
        kind=kind,  # type: ignore[arg-type]
        key=key, name=name, accent=None, path=None, cpu_pct=cpu, rss=rss, panes=(),
    )  # fmt: skip


def snap(web: int, other: int, cpu: float = 10.0) -> DeckSnapshot:
    return DeckSnapshot(
        groups=(grp("config", WEB, "webshop", web, cpu), grp("other", None, "Other", other, 1.0)),
        machine=MACHINE,
    )


def test_rollup_emits_on_minute_change() -> None:
    r = Rollup()
    assert r.add(snap(2, 10, 10.0), 0.0) == []
    assert r.add(snap(4, 20, 30.0), 2.0) == []
    rows = r.add(snap(9, 9), 61.0)
    assert rows == [
        MinuteRow(0, f"project:{WEB}", "webshop", 3, 4, 20.0, 30.0),
        MinuteRow(0, "other", "Other", 15, 20, 1.0, 1.0),
    ]
    assert r.flush() == [
        MinuteRow(1, f"project:{WEB}", "webshop", 9, 9, 10.0, 10.0),
        MinuteRow(1, "other", "Other", 9, 9, 1.0, 1.0),
    ]
    assert r.flush() == []


def test_rollup_ignores_snap_without_machine_and_skips_none_cpu() -> None:
    r = Rollup()
    r.add(replace(snap(1, 1), machine=None), 0.0)
    none_cpu = DeckSnapshot(groups=(grp("git", API, "api-gw", 5, 0.0),), machine=MACHINE)
    r.add(none_cpu, 1.0)
    [api, other] = r.flush()
    assert api.subject == f"project:{API}" and api.ram_mean == 5
    # WezTerm unreachable: no Other group, so Other is the machine's used RAM minus projects
    assert (other.subject, other.ram_mean) == ("other", 16 * GIB - 5)


def test_rollup_waiting_group_is_not_a_subject() -> None:
    r = Rollup()
    waiting = grp("waiting", None, "Waiting on you", 7, 1.0)
    r.add(DeckSnapshot(groups=(waiting, grp("other", None, "Other", 3, 0.0)), machine=MACHINE), 0)
    assert [row.subject for row in r.flush()] == ["other"]


def days(subject: str, name: str, *peaks: tuple[str, int]) -> list[DayPeak]:
    return [DayPeak(subject, name, day, ram, ram / GIB * 10) for day, ram in peaks]


def test_history_rows_fill_days() -> None:
    peaks = days(f"project:{WEB}", "shop", ("2026-10-20", 3 * GIB), ("2026-10-22", 5 * GIB))
    peaks += days("other", "Other", ("2026-09-01", GIB))  # older than 30 days: ignored
    medians = {f"project:{WEB}": (2.0 * GIB, 5.0)}
    [web, other] = history_rows(peaks, medians, {f"project:{WEB}": 3}, snap(GIB, 9), TODAY, "ram")
    assert web == HistoryRow(
        f"project:{WEB}", "webshop", float(GIB), 2.0 * GIB, 5.0 * GIB, 3,
        (None,) * 27 + (3.0 * GIB, None, 5.0 * GIB),
    )  # fmt: skip
    assert (other.name, other.alerts, other.now, other.peak) == ("Other", None, 9.0, None)
    [web_cpu, _] = history_rows(peaks, medians, {}, None, TODAY, "cpu")
    assert (web_cpu.now, web_cpu.median, web_cpu.peak, web_cpu.alerts) == (None, 5.0, 50.0, 0)


def test_history_rows_live_only() -> None:
    [web, other] = history_rows([], {}, {}, snap(GIB, 9), TODAY, "ram")
    assert web.days == (None,) * 30 and web.now == GIB
    assert (web.median, web.peak) == (None, None)


def test_sort_keeps_other_last() -> None:
    def r(name: str, peak: float | None, subject: str = "") -> HistoryRow:
        return HistoryRow(subject or f"project:/{name}", name, None, None, peak, 0, ())

    rows = [r("Other", 99.0, "other"), r("b", 1.0), r("a", None), r("c", 5.0)]
    assert [x.name for x in sort_rows(rows, "peak")] == ["c", "b", "a", "Other"]
    assert [x.name for x in sort_rows(rows, "name")] == ["a", "b", "c", "Other"]


def test_edges_and_legend() -> None:
    t = derive(ResourceSettings(), 32 * GIB, 16)
    e = edges(t, "ram")
    assert e == (2.0 * GIB, 4.0 * GIB, 6.0 * GIB, 8.0 * GIB)
    assert legend(e, "ram") == (
        "peak RAM per day:  ░ <2.0 GB  ▒ 2.0–4.0  ▓ 4.0–6.0  ▓ 6.0–8.0  █ 8.0+"
    )
    c = edges(t, "cpu")
    assert legend(c, "cpu") == "peak CPU per day:  ░ <200%  ▒ 200–400  ▓ 400–600  ▓ 600–800  █ 800+"
    assert [band(v, e) for v in (None, 0.0, 2.0 * GIB, 7.9 * GIB, 8.0 * GIB)] == [-1, 0, 1, 3, 4]
    assert len(CELLS) == 5
