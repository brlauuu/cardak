import threading
from collections.abc import Sequence
from pathlib import Path

import pytest

from cardak.collectors.journal import JournalEntry, parse_entries
from cardak.collectors.machine import MachineSample
from cardak.core.config import Config, NotifySettings
from cardak.core.daemon import Daemon, DaemonRunning, lock, run
from cardak.core.snapshot import DeckSnapshot, Group, PaneRow
from cardak.core.store import MinuteRow, ResourceEvent, StoreError

FIX = Path(__file__).parent / "fixtures" / "journal"
SCOPE = "app-org.wezfurlong.wezterm-0123456789abcdef0123456789abcdef.scope"
Sent = list[tuple[str, str, str, NotifySettings]]

GIB = 2**30
MACHINE = MachineSample(32 * GIB, 16 * GIB, GIB, GIB, 20.0, 16)


def big_snap() -> DeckSnapshot:
    pane = PaneRow(
        pane_id=12, label="pytest", detail=None, cwd=None, cwd_display=".", cpu_pct=5.0,
        rss=9 * GIB, is_self=False,
    )  # fmt: skip
    g = Group(
        kind="config", key=Path("/home/user/repos/webshop"), name="webshop", accent=None,
        path=None, cpu_pct=5.0, rss=9 * GIB, panes=(pane,),
    )  # fmt: skip
    return DeckSnapshot(groups=(g,), machine=MACHINE)


class FakeSource:
    def __init__(self, snap: DeckSnapshot, fail_first: int = 0) -> None:
        self.snap = snap
        self.fail_first = fail_first
        self.config = Config()

    def poll(self) -> DeckSnapshot:
        if self.fail_first:
            self.fail_first -= 1
            raise RuntimeError("wezterm restarting")
        return self.snap


class FakeStore:
    def __init__(self, fail: bool = False) -> None:
        self.events: list[ResourceEvent] = []
        self.minutes: list[MinuteRow] = []
        self.prunes: list[float] = []
        self.fail = fail

    def record_resource_event(self, event: ResourceEvent) -> None:
        if self.fail:
            raise StoreError("disk full")
        self.events.append(event)

    def record_minutes(self, rows: Sequence[MinuteRow]) -> None:
        if self.fail:
            raise StoreError("disk full")
        self.minutes += rows

    def prune(self, now: float) -> None:
        self.prunes.append(now)


def daemon(source: FakeSource, store: FakeStore | None, sent: Sent, **kw: object) -> Daemon:
    return Daemon(source, store, lambda *a: sent.append(a), **kw)  # type: ignore[arg-type]


def alerts(store: FakeStore) -> list[ResourceEvent]:
    return [e for e in store.events if e.kind not in ("daemon", "crash")]


def test_step_records_and_notifies() -> None:
    store, sent = FakeStore(), []
    d = daemon(FakeSource(big_snap()), store, sent)
    d.step(0.0)
    assert [(e.kind, e.subject, e.notified) for e in store.events] == [
        ("big", "project:/home/user/repos/webshop", True)
    ]
    assert [(level, title) for level, title, _, _ in sent] == [
        ("warning", "cardak · webshop is big")
    ]
    d.step(2.0)
    assert len(store.events) == 1 and len(sent) == 1


def test_step_passes_notify_settings() -> None:
    source = FakeSource(big_snap())
    source.config = Config(notify=NotifySettings(desktop=False))
    sent: Sent = []
    daemon(source, None, sent).step(0.0)
    assert [s for *_, s in sent] == [NotifySettings(desktop=False)]


def test_step_without_machine_does_nothing() -> None:
    store, sent = FakeStore(), []
    d = daemon(FakeSource(DeckSnapshot(groups=big_snap().groups)), store, sent)
    assert d.step(0.0) == []
    assert store.events == [] and sent == [] and d.rollup.flush() == []


def test_store_error_does_not_stop_notifying(capsys: pytest.CaptureFixture[str]) -> None:
    sent: Sent = []
    daemon(FakeSource(big_snap()), FakeStore(fail=True), sent).step(0.0)
    assert len(sent) == 1 and "disk full" in capsys.readouterr().err


def test_step_writes_minutes() -> None:
    store = FakeStore()
    d = daemon(FakeSource(big_snap()), store, [])
    for now in (0.0, 2.0, 61.0):
        d.step(now)
    assert [(r.minute, r.subject) for r in store.minutes] == [
        (0, "project:/home/user/repos/webshop"),
        (0, "other"),  # no Other group in the snapshot: the machine's remainder
    ]


def test_stop_flushes_partial_minute() -> None:
    store = FakeStore()
    d = daemon(FakeSource(big_snap()), store, [])
    d.step(0.0)
    d.stopped(30.0)
    assert [r.minute for r in store.minutes] == [0, 0]
    assert store.events[-1] == ResourceEvent(
        30.0, "daemon", "info", "daemon", "cardak daemon stopped", False
    )


def journal_with_oom(
    since: float, until: float
) -> tuple[list[JournalEntry] | None, list[JournalEntry]]:
    kernel = parse_entries((FIX / "kernel.jsonl").read_text())
    return kernel, parse_entries((FIX / "user.jsonl").read_text())


def crash_run(journal: object) -> tuple[FakeStore, Sent]:
    G = 1792589328.0
    store, sent = FakeStore(), []
    guis = iter([{7: SCOPE}, {}, {}])
    d = daemon(FakeSource(DeckSnapshot()), store, sent, guis=lambda: next(guis), journal=journal)
    for now in (G - 2, G, G + 5):
        d.step(now)
    return store, sent


def test_crash_is_recorded_and_notified() -> None:
    store, sent = crash_run(journal_with_oom)
    [crash] = [e for e in store.events if e.kind == "crash"]
    assert (crash.level, crash.subject, crash.notified) == ("critical", "wezterm:7", True)
    assert crash.text.startswith("WezTerm was closed at ")
    assert [(level, title) for level, title, _, _ in sent] == [
        ("critical", "cardak · WezTerm closed: out of memory")
    ]


def test_quiet_crash_not_notified() -> None:
    store, sent = crash_run(lambda since, until: ([], []))
    [crash] = [e for e in store.events if e.kind == "crash"]
    assert (crash.level, crash.notified) == ("info", False) and sent == []


def test_journal_error_keeps_running(capsys: pytest.CaptureFixture[str]) -> None:
    def broken(since: float, until: float) -> tuple[list[JournalEntry] | None, list[JournalEntry]]:
        raise OSError("journal gone")

    store, _ = crash_run(broken)
    assert [e.kind for e in store.events] == ["crash"]
    assert "journal gone" in capsys.readouterr().err


def test_poll_error_keeps_running(capsys: pytest.CaptureFixture[str]) -> None:
    store, stop = FakeStore(), threading.Event()
    clock = iter(float(i * 2) for i in range(1000))
    steps = 0

    def sleep(_s: float) -> None:
        nonlocal steps
        steps += 1
        if steps == 3:
            stop.set()

    source = FakeSource(big_snap(), fail_first=1)
    d = daemon(source, store, [])
    run(d, clock=lambda: next(clock), sleep=sleep, stop=stop)
    assert len(alerts(store)) == 1  # the 2nd step alerted; the 3rd is inside LOG_EVERY_S
    assert "RuntimeError" in capsys.readouterr().err


def test_start_and_stop_events() -> None:
    store, stop = FakeStore(), threading.Event()
    stop.set()
    run(daemon(FakeSource(DeckSnapshot()), store, []), clock=lambda: 5.0, stop=stop)
    assert [e.text for e in store.events] == ["cardak daemon started", "cardak daemon stopped"]


def test_prunes_hourly() -> None:
    store, stop = FakeStore(), threading.Event()
    now = 0.0

    def clock() -> float:
        return now

    def sleep(seconds: float) -> None:
        nonlocal now
        now += seconds
        if now > 3601:
            stop.set()

    run(daemon(FakeSource(DeckSnapshot()), store, []), clock, sleep, stop)
    assert store.prunes == [0.0, 3600.0]


def test_second_daemon_exits(tmp_path: Path) -> None:
    held = lock(tmp_path / "daemon.lock")
    with pytest.raises(DaemonRunning):
        lock(tmp_path / "daemon.lock")
    held.close()
    lock(tmp_path / "daemon.lock").close()


def test_main_exits_with_already_running_when_locked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from cardak.core import daemon

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    held = lock(tmp_path / "cardak" / "daemon.lock")
    try:
        assert daemon.main() == daemon.ALREADY_RUNNING == 3
    finally:
        held.close()
    assert "already running" in capsys.readouterr().err
