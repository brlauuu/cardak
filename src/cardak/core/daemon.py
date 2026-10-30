"""`cardak daemon`: sample every ~2 s; alert, roll up history, report crashes; the user service.

It polls through LiveSource like the deck (no store, no theme writes, no summaries), so the
numbers match what the deck shows. It never imports Textual.
"""

import fcntl
import signal
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import IO, TYPE_CHECKING, Protocol

from cardak.collectors.journal import JournalEntry
from cardak.core.config import Config, NotifySettings, Theme
from cardak.core.crash import JOURNAL_BEFORE_S, CrashWatch, Gone
from cardak.core.history import Rollup
from cardak.core.resources import Thresholds, derive
from cardak.core.snapshot import DeckSnapshot
from cardak.core.store import MinuteRow, ResourceEvent, StoreError
from cardak.core.watch import AlertKind, ResourceAlert, Watcher

if TYPE_CHECKING:
    from cardak.core.source import LiveSource
    from cardak.core.spend import MonthSpend

INTERVAL = 2.0
ALREADY_RUNNING = 3  # the unit's RestartPreventExitStatus: systemd must not retry it
PRUNE_EVERY_S = 3600.0

Notify = Callable[[str, str, str, NotifySettings, Theme], None]  # level, title, text, …
# which sounds an alert plays (#91): everything about RAM is memory; the crash report too
THEME_BY_KIND: Mapping[AlertKind, Theme] = {
    "memory": "memory",
    "big": "memory",
    "growth": "memory",
    "busy": "other",
    "machine_busy": "other",
}
Journal = Callable[[float, float], tuple[list[JournalEntry] | None, list[JournalEntry]]]
Guis = Callable[[], Mapping[int, str | None]]
Publish = Callable[[DeckSnapshot, float], None]  # the web view's copy of each snapshot (M7)
WebStart = Callable[["LiveSource"], Publish | None]  # cli.py's: starts the server, or None


class Poller(Protocol):
    @property
    def config(self) -> Config: ...

    def poll(self) -> DeckSnapshot: ...


class EventStore(Protocol):
    def record_resource_event(self, event: ResourceEvent) -> None: ...

    def record_minutes(self, rows: Sequence[MinuteRow]) -> None: ...

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


def log(message: str) -> None:
    print(
        f"{datetime.now().isoformat(timespec='seconds')} cardak daemon: {message}", file=sys.stderr
    )


def _no_journal(since: float, until: float) -> tuple[list[JournalEntry] | None, list[JournalEntry]]:
    return None, []


@dataclass
class Daemon:
    source: Poller
    store: EventStore | None
    notify: Notify
    watcher: Watcher = field(default_factory=Watcher)
    rollup: Rollup = field(default_factory=Rollup)
    crashes: CrashWatch = field(default_factory=CrashWatch)
    guis: Guis = dict
    journal: Journal = _no_journal
    publish: Publish | None = None

    def _store(self, what: str, write: Callable[[EventStore], None]) -> None:
        if self.store is None:
            return
        try:
            write(self.store)
        except StoreError as e:
            log(f"{what}: {e}")

    def _event(self, event: ResourceEvent) -> None:
        self._store("event log", lambda s: s.record_resource_event(event))

    def _minutes(self, rows: Sequence[MinuteRow]) -> None:
        if rows:
            self._store("history", lambda s: s.record_minutes(rows))

    def step(self, now: float) -> list[ResourceAlert]:
        guis = self.guis()
        snap = self.source.poll()
        self._publish(snap, now)
        config = self.source.config
        self.crashes.observe(guis, snap, now)
        self._minutes(self.rollup.add(snap, now))
        m = snap.machine
        t = derive(config.resources, m.mem_total, m.n_cpus) if m is not None else None
        alerts = self.watcher.step(snap, t, now) if t is not None else []
        for a in alerts:
            self._event(ResourceEvent(a.ts, a.kind, a.level, a.subject, a.text, a.notify))
            if a.notify:
                theme = THEME_BY_KIND.get(a.kind, "other")
                self.notify(a.level, a.title, a.text, config.notify, theme)
        for gone in self.crashes.due(now):
            self._crash(gone, t, now, config.notify)
        return alerts

    def _publish(self, snap: DeckSnapshot, now: float) -> None:
        if self.publish is None:
            return
        try:
            self.publish(snap, now)
        except Exception as e:  # the web view never costs an alert
            log(f"web view: {e!r}")

    def _crash(
        self, gone: Gone, t: Thresholds | None, now: float, settings: NotifySettings
    ) -> None:
        try:
            kernel, user = self.journal(gone.ts - JOURNAL_BEFORE_S, now)
        except Exception as e:  # a journal problem costs the details, never the report
            log(f"journal: {e!r}")
            kernel, user = None, []
        rep = self.crashes.report(gone, kernel, user, t)
        level = "critical" if rep.loud else "info"
        self._event(ResourceEvent(now, "crash", level, f"wezterm:{gone.pid}", rep.text, rep.loud))
        if rep.loud:
            self.notify(level, rep.title, rep.text, settings, "memory")

    def prune(self, now: float) -> None:
        self._store("prune", lambda s: s.prune(now))

    def started(self, now: float) -> None:
        self._event(ResourceEvent(now, "daemon", "info", "daemon", "cardak daemon started", False))

    def stopped(self, now: float) -> None:
        self._minutes(self.rollup.flush())
        self._event(ResourceEvent(now, "daemon", "info", "daemon", "cardak daemon stopped", False))


def run(
    daemon: Daemon,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], object] = time.sleep,
    stop: threading.Event | None = None,
) -> None:
    stop = stop or threading.Event()
    daemon.started(clock())
    pruned: float | None = None
    while not stop.is_set():
        now = clock()
        if pruned is None or now - pruned >= PRUNE_EVERY_S:
            daemon.prune(now)
            pruned = now
        try:
            daemon.step(now)
        except Exception as e:  # WezTerm restarting, a collector bug: log and keep watching
            log(repr(e))
        sleep(INTERVAL)
    daemon.stopped(clock())


def _notify(level: str, title: str, text: str, settings: NotifySettings, theme: Theme) -> None:
    from cardak.core.alerts import resource_alert

    resource_alert(level, title, text, settings, theme=theme)


def build_source(
    config_file: Path, agents: bool, spend: "Callable[[], MonthSpend | None] | None" = None
) -> "LiveSource":
    """The daemon's source; with agents (the web view on), agent status read-only (M7).

    Read-only means a wait the focus history saw is shown as seen and nothing is written: only
    the deck marks waits seen.
    """
    from cardak.core.source import LiveSource

    spent = spend or (lambda: None)
    if not agents:
        return LiveSource(config_file, spend=spent)
    from cardak.core.config import anthropic_key_path
    from cardak.core.focus import focus_path, read_history
    from cardak.core.limits import limits_path
    from cardak.core.store import Store, db_path

    try:
        store: Store | None = Store(db_path())  # its own connection: requests read through it
    except StoreError as e:
        log(f"web view without agent status: {e}")
        store = None
    return LiveSource(
        config_file,
        store=store,
        focus_history=lambda: read_history(focus_path()),
        key_file=anthropic_key_path(),
        write_seen=False,
        spend=spent,
        limits_file=limits_path(),
    )


def main(web: WebStart | None = None) -> int:
    from cardak.core.config import config_path, state_dir
    from cardak.core.store import Store, db_path

    try:
        held = lock(state_dir() / "daemon.lock")
    except DaemonRunning:
        print("cardak daemon: already running", file=sys.stderr)
        return ALREADY_RUNNING
    try:
        store: Store | None = Store(db_path())
    except StoreError as e:
        log(f"running without the event log: {e}")
        store = None
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    from cardak.collectors.journal import read_journal
    from cardak.collectors.wezterm import gui_processes
    from cardak.core import spend

    counted = spend.background(log)
    source = build_source(
        config_path(None), agents=web is not None, spend=counted[0] if counted else None
    )
    publish = web(source) if web is not None else None
    daemon = Daemon(
        source, store, _notify, guis=gui_processes, journal=read_journal, publish=publish
    )
    run(daemon, sleep=stop.wait, stop=stop)
    if counted is not None:
        counted[1].set()
    held.close()
    return 0
