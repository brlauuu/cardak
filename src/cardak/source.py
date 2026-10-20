"""Wire config, resolver and collectors into snapshots for the UI."""

import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from pathlib import Path

from cardak import config as config_mod
from cardak.collectors import claude, wezterm
from cardak.collectors.processes import PaneProcs, ProcessSampler
from cardak.config import Config, ConfigError
from cardak.resolver import GitLookup, ProjectResolver, run_git
from cardak.snapshot import ClaudeLookup, DeckSnapshot, build_snapshot

ListPanes = Callable[[Mapping[str, str]], list[wezterm.Pane]]
Sample = Callable[[Iterable[str]], dict[str, PaneProcs]]
Stamp = tuple[int, int, int] | None


class LiveSource:
    def __init__(
        self,
        config_file: Path,
        env: Mapping[str, str] | None = None,
        list_panes: ListPanes = wezterm.list_panes,
        sample: Sample | None = None,
        claude_lookup: ClaudeLookup = claude.session_meta,
        git: GitLookup = run_git,
        home: Path | None = None,
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
        return (st.st_mtime_ns, st.st_size, st.st_ino)

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
        self._resolver = ProjectResolver(cfg, self._git)

    def poll(self) -> DeckSnapshot:
        self._reload_if_changed()
        try:
            panes = self._list_panes(self._env)
        except wezterm.WeztermUnavailable as e:
            return DeckSnapshot(error=str(e), config_error=self._config_error)
        procs = self._sample([p.tty for p in panes if p.tty])
        snap = build_snapshot(
            panes, procs, self._resolver, self._claude_lookup, self._self_pane, self._home
        )
        return replace(snap, config_error=self._config_error)

    def add_project(self, name: str, path: Path) -> None:
        config_mod.add_project(self.config_file, name, path)
        self._reload_if_changed()
