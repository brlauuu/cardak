"""Wire config, resolver and collectors into snapshots for the UI."""

import os
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Protocol

from cardak.collectors import claude, wezterm
from cardak.collectors.docker import DockerSampler, DockerWork
from cardak.collectors.machine import MachineSample, MachineSampler
from cardak.collectors.processes import PaneProcs, ProcessSampler
from cardak.core import config as config_mod
from cardak.core.accounting import account
from cardak.core.agents import FocusChange, SessionState, focus_seen_at, mark_seen
from cardak.core.alerts import bundled_sounds, check
from cardak.core.config import Config, ConfigError
from cardak.core.recent import Recent, decorate
from cardak.core.resolver import GitLookup, ProjectResolver, Resolved, run_git
from cardak.core.resources import derive
from cardak.core.snapshot import ClaudeLookup, DeckSnapshot, Group, build_snapshot, group_for
from cardak.core.spend import MonthSpend
from cardak.core.store import StoreError
from cardak.core.summary import SummaryInfo, key_warning, month_start
from cardak.core.themes import Scheme, project_accent

ListPanes = Callable[[Mapping[str, str]], list[wezterm.Pane]]
Sample = Callable[[Iterable[str]], dict[str, PaneProcs]]
Stamp = tuple[int, int, int, int] | None
GroupKey = tuple[str, Path | None]  # (kind, key): what a cwd's spend is attached by


class AgentStore(Protocol):
    def live_sessions(self) -> list[SessionState]: ...

    def get(self, session_id: str) -> SessionState | None: ...

    def mark_seen(self, session_id: str, now: float, wait_since: float | None = None) -> None: ...

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
        machine: Callable[[], MachineSample | None] | None = None,
        docker: Callable[[], Sequence[DockerWork]] | None = None,
        write_seen: bool = True,
        spend: Callable[[], MonthSpend | None] = lambda: None,
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
        self._write_seen = write_seen  # False: show focus-seen waits as seen, write nothing (M7)
        self._clock = clock
        self._key_file = key_file  # None: no waiting summaries in this deck
        self._machine = machine or MachineSampler().sample
        self._docker = docker or DockerSampler().sample
        self._spend = spend  # the counter's latest figure (core/spend.py), read every poll
        self._spend_seen: tuple[MonthSpend, ProjectResolver, dict[GroupKey, float]] | None = None
        self._recent = Recent()
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
    def config(self) -> Config:
        with self._lock:
            return self._config

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
            live = self._seen(self._store, self._store.live_sessions())
        except StoreError as e:
            self._store_warning = f"agent status unavailable: {e}"
            return []
        self._store_warning = None
        return live

    def _seen(self, store: AgentStore, live: list[SessionState]) -> list[SessionState]:
        """Mark focus-seen waits in the store, or with write_seen off only in what is returned."""
        history, now = self._focus_history(), self._clock()
        seen = {s.session_id: at for s in live if (at := focus_seen_at(s, history, now))}
        if not seen:
            return live
        if not self._write_seen:  # as seen when it was seen, so its age doesn't restart each poll
            return [mark_seen(s, seen[s.session_id]) if s.session_id in seen else s for s in live]
        for s in live:
            if s.session_id in seen:
                store.mark_seen(s.session_id, now, s.status_since)
        return store.live_sessions()

    def _summary_store(self) -> AgentStore | None:
        """The store to read summaries from, or None when this deck shows none."""
        if self._key_file is None or not self._config.summary.enabled:
            return None
        if not self._key_file.exists():  # no key, no summaries: no spend line either (#52)
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
            self._summary_warning = f"waiting summaries unavailable: {e}"
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
                        self._store.mark_seen(s.session_id, now, s.status_since)
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

    def _warnings_now(self, spend: MonthSpend | None = None) -> tuple[str, ...]:
        extra = (self._store_warning,) if self._store_warning else ()
        key = key_warning(self._key_file) if self._key_file is not None else None
        summary = (self._summary_warning,) if self._summary_warning else ()
        unpriced = (
            (f"no API price for {', '.join(spend.unpriced)}; left out of this month's figure",)
            if spend is not None and spend.unpriced
            else ()
        )
        return (
            *self._warnings,
            *self._notify_warnings,
            *extra,
            *((key,) if key else ()),
            *summary,
            *unpriced,
        )

    def _spend_by_group(self, spend: MonthSpend) -> dict[GroupKey, float]:
        """Each cwd's spend under the project it resolves to; resolved again only when the
        figure or the config changed, not every poll."""
        seen = self._spend_seen
        if seen is not None and seen[0] is spend and seen[1] is self._resolver:
            return seen[2]
        found: dict[GroupKey, float] = {}
        for cwd, usd in spend.by_cwd.items():
            r = self._resolver.resolve(Path(cwd) if cwd else None)
            found[(r.kind, r.key)] = found.get((r.kind, r.key), 0.0) + usd
        self._spend_seen = (spend, self._resolver, found)
        return found

    def _with_spend(self, snap: DeckSnapshot) -> DeckSnapshot:
        spend = self._spend()
        if spend is None or spend.since != month_start(self._clock()):
            return snap  # not counted yet, or a stalled counter's figure from last month
        by_group = self._spend_by_group(spend)
        groups = tuple(
            g if g.kind == "waiting" else replace(g, spend_usd=by_group.get((g.kind, g.key)))
            for g in snap.groups
        )
        return replace(
            snap,
            groups=groups,
            month_spend=spend.total,
            spend_unpriced=spend.unpriced,
            config_warnings=self._warnings_now(spend),
        )

    def _sample_machine(self) -> MachineSample | None:
        try:
            return self._machine()
        except Exception:  # a sampler bug costs the header, never the deck
            return None

    def _sample_docker(self) -> Sequence[DockerWork]:
        try:
            return self._docker()
        except Exception:
            return []

    def _make_group(self, root: Path) -> Group:
        return group_for(self._resolver.resolve(root), self._home, self._accent_for)

    def _resources(self, snap: DeckSnapshot) -> DeckSnapshot:
        """Docker attributed, Other as the remainder, sparklines and threshold marks."""
        machine = self._sample_machine()
        if snap.error is None:
            roots = [p.path for p in self._config.projects]
            roots += [g.key for g in snap.groups if g.kind == "git" and g.key]
            snap = account(snap, self._sample_docker(), machine, roots, self._make_group)
        else:
            snap = replace(snap, machine=machine)
        self._recent.record(snap)
        if machine is None:
            return snap
        t = derive(self._config.resources, machine.mem_total, machine.n_cpus)
        return replace(decorate(snap, self._recent, t), thresholds=t)

    def _poll(self) -> DeckSnapshot:
        return self._with_spend(self._poll_panes())

    def _poll_panes(self) -> DeckSnapshot:
        self._reload_if_changed()
        try:
            panes = self._list_panes(self._env)
        except wezterm.WeztermUnavailable as e:
            return self._resources(
                DeckSnapshot(
                    error=str(e),
                    config_error=self._config_error,
                    config_warnings=self._warnings_now(),
                )
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
            self._resources(snap),
            config_error=self._config_error,
            config_warnings=self._warnings_now(),
            summary_capped=capped,
            summary_limit=self._config.summary.monthly_limit_usd,
        )

    def add_project(self, name: str, path: Path) -> None:
        with self._lock:
            config_mod.add_project(self.config_file, name, path)
            self._reload_if_changed()
