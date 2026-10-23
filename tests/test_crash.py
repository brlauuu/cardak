import time
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from cardak.collectors.journal import JournalEntry, parse_entries
from cardak.collectors.machine import MachineSample
from cardak.core.crash import CHECK_AFTER_S, CrashWatch, Gone
from cardak.core.snapshot import DeckSnapshot, Group, PaneRow

FIX = Path(__file__).parent / "fixtures" / "journal"
SCOPE = "app-org.wezfurlong.wezterm-0123456789abcdef0123456789abcdef.scope"
OTHER_SCOPE = "app-org.wezfurlong.wezterm-89abcdef0123456789abcdef01234567.scope"
GIB = 2**30
G = 1792589328.0  # 13:28:48 UTC, two seconds after the fixture's OOM kill
CALM = MachineSample(32 * GIB, 16 * GIB, GIB, GIB, 20.0, 16)
FULL = MachineSample(32 * GIB, int(0.3 * GIB), GIB, 0, 95.0, 16)


@pytest.fixture(autouse=True)
def utc(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def kernel() -> list[JournalEntry]:
    return parse_entries((FIX / "kernel.jsonl").read_text())


def user() -> list[JournalEntry]:
    return parse_entries((FIX / "user.jsonl").read_text())


def pane(pane_id: int, label: str, rss: float, pids: frozenset[int] = frozenset()) -> PaneRow:
    return PaneRow(
        pane_id=pane_id, label=label, detail=None, cwd=None, cwd_display=".", cpu_pct=1.0,
        rss=int(rss * GIB), is_self=False, pids=pids,
    )  # fmt: skip


def snap(pytest_gib: float, machine: MachineSample = FULL) -> DeckSnapshot:
    api = Group(
        kind="config", key=Path("/home/user/repos/api-gw"), name="api-gw", accent=None, path=None,
        cpu_pct=100.0, rss=int((pytest_gib + 1.2) * GIB),
        panes=(pane(12, "pytest", pytest_gib, frozenset({41870, 41872})), pane(13, "zsh", 1.2)),
    )  # fmt: skip
    other = Group(
        kind="other", key=None, name="Other", accent=None, path=None, cpu_pct=5.0,
        rss=int(9.9 * GIB), panes=(pane(20, "zsh", 0.1),), outside_rss=int(9.8 * GIB),
    )  # fmt: skip
    return DeckSnapshot(groups=(api, other), machine=machine)


def crashed(guis_before: dict[int, str | None] | None = None) -> tuple[CrashWatch, Gone]:
    w = CrashWatch()
    before = guis_before or {7: SCOPE}
    w.observe(before, snap(2.1), G - 366)
    w.observe(before, snap(13.9), G - 6)
    w.observe({}, DeckSnapshot(error="no WezTerm GUI socket found", machine=FULL), G)
    [gone] = w.due(G + CHECK_AFTER_S)
    return w, gone


def test_gone_after_check_delay() -> None:
    w = CrashWatch()
    w.observe({7: SCOPE}, snap(1.0), 0.0)
    w.observe({}, snap(1.0), 10.0)
    assert w.due(14.0) == []
    assert w.due(15.0) == [Gone(7, SCOPE, 10.0)]
    assert w.due(30.0) == []


def test_report_oom_full_text() -> None:
    w, gone = crashed()
    rep = w.report(gone, kernel(), user(), None)
    assert rep.loud and rep.title == "cardak · WezTerm closed: out of memory"
    assert rep.text == (
        "WezTerm was closed at 13:28:48 on Oct 21. "
        "The machine ran out of memory (32 GB; swap full). "
        "The kernel killed `pytest` (pid 41872) in api-gw, pane 12, which grew from 2.1 GB to "
        "13.9 GB in 6 minutes. "
        "systemd then stopped the whole WezTerm window, because its policy stops an app when one "
        "of its processes is killed (`OOMPolicy=stop`). "
        "The 3 biggest users then: api-gw 15.1 GB, outside WezTerm 9.8 GB, pane 20 · zsh 0.1 GB. "
        "To keep the window open next time, run `cardak resources install` and add the drop-in "
        "it prints."
    )


def test_one_of_two_guis_dies() -> None:
    w = CrashWatch()
    both = {7: SCOPE, 8: OTHER_SCOPE}
    w.observe(both, snap(13.9), G - 6)
    w.observe({7: SCOPE}, snap(13.9), G)  # the other window closed; its scope saw no OOM
    [gone] = w.due(G + 5)
    assert gone.pid == 8
    rep = w.report(gone, kernel(), user(), None)
    assert not rep.loud and rep.title == "cardak · WezTerm closed"
    assert rep.text.startswith("WezTerm (pid 8) closed at 13:28:48 on Oct 21; the journal shows ")


def test_report_quiet_exit() -> None:
    w, gone = crashed()
    rep = w.report(gone, [], [], None)
    assert not rep.loud
    assert rep.text == (
        "WezTerm (pid 7) closed at 13:28:48 on Oct 21; the journal shows no OOM kill. "
        "The 3 biggest users then: api-gw 15.1 GB, outside WezTerm 9.8 GB, pane 20 · zsh 0.1 GB."
    )


def test_report_without_journal_critical() -> None:
    w, gone = crashed()
    rep = w.report(gone, None, [], None)
    assert rep.loud
    assert rep.text.startswith(
        "WezTerm was closed at 13:28:48 on Oct 21. The journal wasn't readable, but memory was "
        "critical just before (0.3 GB free of 32 GB). The 3 biggest users then: "
    )


def test_report_without_journal_calm() -> None:
    w = CrashWatch()
    w.observe({7: SCOPE}, snap(2.0, CALM), 0.0)
    w.observe({}, snap(2.0, CALM), 2.0)
    [gone] = w.due(7.0)
    rep = w.report(gone, None, [], None)
    assert not rep.loud
    assert rep.text.startswith("WezTerm (pid 7) closed at 00:00:02 on Jan 1; the journal wasn't")


def test_unknown_pid_sentence() -> None:
    w = CrashWatch()
    w.observe({7: None}, DeckSnapshot(machine=FULL), G - 2)  # scope unknown, no panes seen
    w.observe({}, DeckSnapshot(machine=FULL), G)
    [gone] = w.due(G + 5)
    rep = w.report(gone, kernel(), [], None)
    assert rep.loud
    assert (
        "The kernel killed `pytest` (pid 41872), which cardak didn't see in a WezTerm pane."
        in rep.text
    )
    assert "OOMPolicy" not in rep.text and "biggest users" not in rep.text


def test_old_pids_and_series_are_dropped() -> None:
    w = CrashWatch()
    w.observe({7: SCOPE}, snap(2.0), 0.0)
    gone_pane = replace(snap(2.0), groups=(snap(2.0).groups[1],))
    w.observe({7: SCOPE}, gone_pane, 1000.0)
    w.observe({}, gone_pane, 1002.0)
    [gone] = w.due(1010.0)
    rep = w.report(replace(gone, ts=G), kernel(), user(), None)
    assert "which cardak didn't see in a WezTerm pane" in rep.text
