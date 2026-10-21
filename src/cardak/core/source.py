"""Wire config, resolver and collectors into snapshots for the UI."""

import os
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Protocol

from cardak.collectors import claude, wezterm
from cardak.collectors.processes import PaneProcs, ProcessSampler
from cardak.core import config as config_mod
from cardak.core.agents import FocusChange, SessionState, seen_by_focus
from cardak.core.alerts import bundled_sounds, check
from cardak.core.config import Config, ConfigError
from cardak.core.resolver import GitLookup, ProjectResolver, Resolved, run_git
from cardak.core.snapshot import ClaudeLookup, DeckSnapshot, build_snapshot
from cardak.core.store import StoreError
from cardak.core.summary import SummaryInfo, key_warning, month_start
from cardak.core.themes import Scheme, project_accent

ListPanes = Callable[[Mapping[str, str]], list[wezterm.Pane]]
Sample = Callable[[Iterable[str]], dict[str, PaneProcs]]
Stamp = tuple[int, int, int, int] | None


class AgentStore(Protocol):
    def live_sessions(self) -> list[SessionState]: ...

    def get(self, session_id: str) -> SessionState | None: ...

    def mark_seen(self, session_id: str, now: float) -> None: ...

    def summaries_for(self, waits: Iterable[tuple[str, float]]) -> dict[str, str]: ...

    def spend_since(self, ts: float) -> float: ...

    def latest_error_since(self, ts: float) -> str | None: ...


class LiveSource:
    """Polled from a worker thread while add_project runs on the UI thread; one lock covers both."""

    def __init__(
        self,
        config_file: Path,
        env: Mapping[str, str] | None = None,
        list_panes: ListPanes = wezterm.list_panes,
        sample: Sample | None = None,
        claude_lookup: ClaudeLookup = claude.session_meta,
        git: GitLookup = run_git,
        home: Path | None = None,
        on_config: Callable[[Config], Sequence[str]] | None = None,
        schemes: Callable[[], Mapping[str, Scheme]] | None = None,
        store: AgentStore | None = None,
        focus_history: Callable[[], Sequence[FocusChange]] = lambda: [],
        clock: Callable[[], float] = time.time,
        key_file: Path | None = None,
    ) -> None:
        self.config_file = config_file
        self._env = os.environ if env is None else env
        self._list_panes = list_panes
        self._sample = sample or ProcessSampler().sample
        self._claude_lookup = claude_lookup
        self._git = git
        self._home = home or Path.home()
        pane = self._env.get("WEZTERM_PANE", "")
        self._self_pane = int(pane) if pane.isdigit() else None
        self._lock = threading.Lock()
        self._on_config = on_config
        self._schemes: Callable[[], Mapping[str, Scheme]] = schemes or (lambda: {})
        self._store = store
        self._focus_history = focus_history
        self._clock = clock
        self._key_file = key_file  # None: no waiting summaries in this deck
        self._summary_warning: str | None = None
        self._notify_warnings: tuple[str, ...] = ()
        self._store_warning: str | None = None
        self._config = Config()
        self._warnings: tuple[str, ...] = ()
        self._config_error: str | None = None
        self._resolver = ProjectResolver(Config(), git)
        self._stamp: Stamp | bool = False  # never equal to a real stamp, so the first check loads
        self._reload_if_changed()

    @property
    def can_add(self) -> bool:
        return self._config_error is None

    def _current_stamp(self) -> Stamp:
        try:
            st = self.config_file.stat()
        except FileNotFoundError:
            return None
        # ctime too: a chmod that makes the file readable again changes ctime, not mtime.
        return (st.st_ctime_ns, st.st_mtime_ns, st.st_size, st.st_ino)

    def _reload_if_changed(self) -> None:
        stamp = self._current_stamp()
        if stamp == self._stamp:
            return
        self._stamp = stamp
        try:
            cfg = config_mod.load(self.config_file)
            self._config_error = None
        except ConfigError as e:
            cfg = Config()
            self._config_error = str(e)
            self._warnings = ()
        self._config = cfg
        self._notify_warnings = tuple(check(cfg.notify, bundled_sounds()))
        self._resolver = ProjectResolver(cfg, self._git)
        if self._config_error is None:
            self._run_on_config()

    def _run_on_config(self) -> None:
        if self._on_config is None:
            return
        try:
            self._warnings = tuple(self._on_config(self._config))
        except Exception as e:  # theme files are a side effect; never break the deck over them
            self._warnings = (f"couldn't update theme files: {e!r}",)

    def regenerate(self) -> None:
        """Re-run on_config (e.g. once the scheme list has loaded)."""
        with self._lock:
            if self._config_error is None:
                self._run_on_config()

    def _accent_for(self, resolved: Resolved) -> str | None:
        project = next((p for p in self._config.projects if p.path == resolved.key), None)
        return project_accent(project, self._schemes()) if project else None

    def set_theme(self, project_path: Path, theme: str, name: str | None = None) -> None:
        with self._lock:
            if any(p.path == project_path for p in self._config.projects):
                config_mod.set_project_theme(self.config_file, project_path, theme)
            else:
                config_mod.add_project(
                    self.config_file, name or project_path.name, project_path, theme=theme
                )
            self._reload_if_changed()

    def poll(self) -> DeckSnapshot:
        with self._lock:
            return self._poll()

    def _sessions(self) -> list[SessionState]:
        """Live sessions, after marking the ones the user has looked at as seen."""
        if self._store is None:
            return []
        try:
            live = self._store.live_sessions()
            history, now = self._focus_history(), self._clock()
            seen = [s for s in live if seen_by_focus(s, history, now)]
            for s in seen:
                self._store.mark_seen(s.session_id, now)
            if seen:
                live = self._store.live_sessions()
        except StoreError as e:
            self._store_warning = f"agent status unavailable: {e}"
            return []
        self._store_warning = None
        return live

    def _summary_store(self) -> AgentStore | None:
        """The store to read summaries from, or None when this deck shows none."""
        if self._key_file is None or not self._config.summary.enabled:
            return None
        return self._store

    def _summaries(self, live: Sequence[SessionState]) -> tuple[dict[str, str], bool]:
        """Summaries of the current waits, and whether this month's spend reached the cap."""
        self._summary_warning = None
        store = self._summary_store()
        if store is None:
            return {}, False
        waits = [(s.session_id, s.status_since) for s in live if s.status == "waiting"]
        start = month_start(self._clock())
        try:
            found = store.summaries_for(waits)
            spend = store.spend_since(start)
            error = store.latest_error_since(start)
        except StoreError as e:
            self._store_warning = f"agent status unavailable: {e}"
            return {}, False
        if error:
            self._summary_warning = (
                f"waiting summaries failing: {error}; see ~/.local/state/cardak/hook.log"
            )
        return found, spend >= self._config.summary.monthly_limit_usd

    def summary_info(self, state: SessionState) -> SummaryInfo | None:
        """The detail panel's summary lines for a session; None when summaries are off."""
        with self._lock:
            store = self._summary_store()
            if store is None:
                return None
            try:
                found = store.summaries_for([(state.session_id, state.status_since)])
                spend = store.spend_since(month_start(self._clock()))
            except StoreError:
                return None
            limit = self._config.summary.monthly_limit_usd
            return SummaryInfo(found.get(state.session_id), spend, limit)

    def mark_seen_pane(self, pane_id: int) -> None:
        """The user jumped to this pane: its waiting session has been seen."""
        with self._lock:
            if self._store is None:
                return
            now = self._clock()
            try:
                for s in self._store.live_sessions():
                    if s.pane_id == pane_id and s.status == "waiting":
                        self._store.mark_seen(s.session_id, now)
            except StoreError as e:
                self._store_warning = f"agent status unavailable: {e}"

    def session(self, session_id: str) -> SessionState | None:
        with self._lock:
            if self._store is None:
                return None
            try:
                return self._store.get(session_id)
            except StoreError:
                return None

    def _warnings_now(self) -> tuple[str, ...]:
        extra = (self._store_warning,) if self._store_warning else ()
        key = key_warning(self._key_file) if self._key_file is not None else None
        summary = (self._summary_warning,) if self._summary_warning else ()
        return (*self._warnings, *self._notify_warnings, *extra, *((key,) if key else ()), *summary)

    def _poll(self) -> DeckSnapshot:
        self._reload_if_changed()
        try:
            panes = self._list_panes(self._env)
        except wezterm.WeztermUnavailable as e:
            return DeckSnapshot(
                error=str(e), config_error=self._config_error, config_warnings=self._warnings_now()
            )
        procs = self._sample([p.tty for p in panes if p.tty])
        sessions = self._sessions()
        summaries, capped = self._summaries(sessions)
        snap = build_snapshot(
            panes,
            procs,
            self._resolver,
            self._claude_lookup,
            self._self_pane,
            self._home,
            accent_for=self._accent_for,
            sessions=sessions,
            summaries=summaries,
        )
        return replace(
            snap,
            config_error=self._config_error,
            config_warnings=self._warnings_now(),
            summary_capped=capped,
            summary_limit=self._config.summary.monthly_limit_usd,
        )

    def add_project(self, name: str, path: Path) -> None:
        with self._lock:
            config_mod.add_project(self.config_file, name, path)
            self._reload_if_changed()
