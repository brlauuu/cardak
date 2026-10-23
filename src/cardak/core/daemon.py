"""`cardak daemon`: sample every ~2 s, alert, log; runs as the systemd user service.

It polls through LiveSource like the deck (no store, no theme writes, no summaries), so the
numbers match what the deck shows. It never imports Textual.
"""

import fcntl
import signal
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import IO, Protocol

from cardak.core.config import Config, NotifySettings
from cardak.core.resources import derive
from cardak.core.snapshot import DeckSnapshot
from cardak.core.store import ResourceEvent, StoreError
from cardak.core.watch import ResourceAlert, Watcher

INTERVAL = 2.0
PRUNE_EVERY_S = 3600.0

Notify = Callable[[ResourceAlert, NotifySettings], None]


class Poller(Protocol):
    @property
    def config(self) -> Config: ...

    def poll(self) -> DeckSnapshot: ...


class EventStore(Protocol):
    def record_resource_event(self, event: ResourceEvent) -> None: ...

    def prune(self, now: float) -> None: ...


class DaemonRunning(Exception):
    pass


def lock(path: Path) -> IO[str]:
    """Held for the daemon's lifetime: a second daemon would double every alert."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as e:
        handle.close()
        raise DaemonRunning(str(path)) from e
    return handle


def _log(message: str) -> None:
    print(
        f"{datetime.now().isoformat(timespec='seconds')} cardak daemon: {message}", file=sys.stderr
    )


def step(
    source: Poller, watcher: Watcher, store: EventStore | None, notify: Notify, now: float
) -> list[ResourceAlert]:
    snap = source.poll()
    m = snap.machine
    if m is None:
        return []
    config = source.config
    alerts = watcher.step(snap, derive(config.resources, m.mem_total, m.n_cpus), now)
    for a in alerts:
        if store is not None:
            try:
                store.record_resource_event(
                    ResourceEvent(a.ts, a.kind, a.level, a.subject, a.text, a.notify)
                )
            except StoreError as e:
                _log(f"event log: {e}")
        if a.notify:
            notify(a, config.notify)
    return alerts


def run(
    source: Poller,
    watcher: Watcher,
    store: EventStore | None,
    notify: Notify,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], object] = time.sleep,
    stop: threading.Event | None = None,
) -> None:
    stop = stop or threading.Event()
    pruned: float | None = None
    while not stop.is_set():
        now = clock()
        if store is not None and (pruned is None or now - pruned >= PRUNE_EVERY_S):
            try:
                store.prune(now)
            except StoreError as e:
                _log(f"prune: {e}")
            pruned = now
        try:
            step(source, watcher, store, notify, now)
        except Exception as e:  # WezTerm restarting, a collector bug: log and keep watching
            _log(repr(e))
        sleep(INTERVAL)


def _notify(alert: ResourceAlert, settings: NotifySettings) -> None:
    from cardak.core.alerts import resource_alert

    resource_alert(alert.level, alert.title, alert.text, settings)


def main() -> int:
    from cardak.core.config import config_path, state_dir
    from cardak.core.source import LiveSource
    from cardak.core.store import Store, db_path

    try:
        held = lock(state_dir() / "daemon.lock")
    except DaemonRunning:
        print("cardak daemon: already running", file=sys.stderr)
        return 1
    try:
        store: Store | None = Store(db_path())
    except StoreError as e:
        _log(f"running without the event log: {e}")
        store = None
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    run(LiveSource(config_path(None)), Watcher(), store, _notify, sleep=stop.wait, stop=stop)
    held.close()
    return 0
