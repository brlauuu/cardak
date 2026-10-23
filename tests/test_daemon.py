import threading
from pathlib import Path

import pytest

from cardak.collectors.machine import MachineSample
from cardak.core.config import Config, NotifySettings
from cardak.core.daemon import DaemonRunning, lock, run, step
from cardak.core.snapshot import DeckSnapshot, Group, PaneRow
from cardak.core.store import ResourceEvent, StoreError
from cardak.core.watch import ResourceAlert, Watcher

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
        self.prunes: list[float] = []
        self.fail = fail

    def record_resource_event(self, event: ResourceEvent) -> None:
        if self.fail:
            raise StoreError("disk full")
        self.events.append(event)

    def prune(self, now: float) -> None:
        self.prunes.append(now)


def test_step_records_and_notifies() -> None:
    store, sent = FakeStore(), []
    w = Watcher()
    source = FakeSource(big_snap())
    step(source, w, store, lambda a, s: sent.append(a), 0.0)
    assert [(e.kind, e.subject, e.notified) for e in store.events] == [
        ("big", "project:/home/user/repos/webshop", True)
    ]
    assert [a.kind for a in sent] == ["big"]
    step(source, w, store, lambda a, s: sent.append(a), 2.0)
    assert len(store.events) == 1 and len(sent) == 1


def test_step_passes_notify_settings() -> None:
    source = FakeSource(big_snap())
    source.config = Config(notify=NotifySettings(desktop=False))
    seen: list[NotifySettings] = []
    step(source, Watcher(), None, lambda a, s: seen.append(s), 0.0)
    assert seen == [NotifySettings(desktop=False)]


def test_step_without_machine_does_nothing() -> None:
    store, sent = FakeStore(), []
    source = FakeSource(DeckSnapshot(groups=big_snap().groups))
    assert step(source, Watcher(), store, lambda a, s: sent.append(a), 0.0) == []
    assert store.events == [] and sent == []


def test_store_error_does_not_stop_notifying(capsys: pytest.CaptureFixture[str]) -> None:
    sent: list[ResourceAlert] = []
    step(FakeSource(big_snap()), Watcher(), FakeStore(fail=True), lambda a, s: sent.append(a), 0.0)
    assert len(sent) == 1 and "disk full" in capsys.readouterr().err


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
    run(
        source,
        Watcher(),
        store,
        lambda a, s: None,
        clock=lambda: next(clock),
        sleep=sleep,
        stop=stop,
    )
    assert len(store.events) == 1  # the 2nd step alerted; the 3rd is inside LOG_EVERY_S
    assert "RuntimeError" in capsys.readouterr().err


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

    run(FakeSource(DeckSnapshot()), Watcher(), store, lambda a, s: None, clock, sleep, stop)
    assert store.prunes == [0.0, 3600.0]


def test_second_daemon_exits(tmp_path: Path) -> None:
    held = lock(tmp_path / "daemon.lock")
    with pytest.raises(DaemonRunning):
        lock(tmp_path / "daemon.lock")
    held.close()
    lock(tmp_path / "daemon.lock").close()
