"""Wire config, resolver and collectors into snapshots for the UI."""

import os
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path

from cardak.collectors import claude, wezterm
from cardak.collectors.processes import PaneProcs, ProcessSampler
from cardak.core import config as config_mod
from cardak.core.config import Config, ConfigError
from cardak.core.resolver import GitLookup, ProjectResolver, Resolved, run_git
from cardak.core.snapshot import ClaudeLookup, DeckSnapshot, build_snapshot
from cardak.core.themes import Scheme, project_accent

ListPanes = Callable[[Mapping[str, str]], list[wezterm.Pane]]
Sample = Callable[[Iterable[str]], dict[str, PaneProcs]]
Stamp = tuple[int, int, int, int] | None


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

    def _poll(self) -> DeckSnapshot:
        self._reload_if_changed()
        try:
            panes = self._list_panes(self._env)
        except wezterm.WeztermUnavailable as e:
            return DeckSnapshot(
                error=str(e), config_error=self._config_error, config_warnings=self._warnings
            )
        procs = self._sample([p.tty for p in panes if p.tty])
        snap = build_snapshot(
            panes,
            procs,
            self._resolver,
            self._claude_lookup,
            self._self_pane,
            self._home,
            accent_for=self._accent_for,
        )
        return replace(snap, config_error=self._config_error, config_warnings=self._warnings)

    def add_project(self, name: str, path: Path) -> None:
        with self._lock:
            config_mod.add_project(self.config_file, name, path)
            self._reload_if_changed()
