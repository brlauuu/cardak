import threading
from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path

from cardak.collectors.processes import PaneProcs
from cardak.collectors.wezterm import Pane, WeztermUnavailable
from cardak.core.agents import FocusChange, SessionState, mark_seen
from cardak.core.config import Config
from cardak.core.source import LiveSource
from cardak.core.store import StoreError
from cardak.core.summary import SummaryInfo

HOME = Path("/home/user")


def panes(env: Mapping[str, str]) -> list[Pane]:
    return [
        Pane(0, 0, 0, "default", "zsh", HOME / "repos/webshop", "/dev/pts/1", True),
        Pane(1, 0, 0, "default", "zsh", HOME / "notes", "/dev/pts/2", False),
    ]


def unavailable(env: Mapping[str, str]) -> list[Pane]:
    raise WeztermUnavailable("no WezTerm GUI socket found")


def sample(ttys: Iterable[str]) -> dict[str, PaneProcs]:
    return {t: PaneProcs("zsh", 1, None, 1.0, 10, 1, 0.0, 0) for t in ttys}


def no_git(cwd: Path) -> Path | None:
    return None


def make(cfg: Path, list_panes=panes, env: dict[str, str] | None = None) -> LiveSource:
    return LiveSource(
        cfg,
        env={"WEZTERM_PANE": "1"} if env is None else env,
        list_panes=list_panes,
        sample=sample,
        claude_lookup=lambda pid: None,
        git=no_git,
        home=HOME,
    )


def names(source: LiveSource) -> list[str]:
    return [g.name for g in source.poll().groups]


def test_poll_groups_and_marks_self(tmp_path: Path) -> None:
    cfg = tmp_path / "projects.toml"
    cfg.write_text(f'[[project]]\nname = "webshop"\npath = "{HOME}/repos/webshop"\n')
    snap = make(cfg).poll()
    assert [g.name for g in snap.groups] == ["webshop", "Other"]
    assert snap.groups[1].panes[0].is_self
    assert snap.config_error is None


def test_unreachable_wezterm(tmp_path: Path) -> None:
    snap = make(tmp_path / "p.toml", list_panes=unavailable).poll()
    assert snap.error == "no WezTerm GUI socket found"
    assert snap.groups == ()


def test_broken_config_reports_and_blocks_add(tmp_path: Path) -> None:
    cfg = tmp_path / "projects.toml"
    cfg.write_text("[[project]]\nname = \n")
    source = make(cfg, env={})
    snap = source.poll()
    assert snap.config_error is not None and snap.config_error.startswith("projects.toml line 2")
    assert not source.can_add
    assert names(source) == ["Other"]


def test_add_project_regroups(tmp_path: Path) -> None:
    cfg = tmp_path / "projects.toml"
    source = make(cfg)
    assert names(source) == ["Other"]
    source.add_project("notes", HOME / "notes")
    assert names(source) == ["notes", "Other"]


def test_hand_edit_is_picked_up(tmp_path: Path) -> None:
    cfg = tmp_path / "projects.toml"
    cfg.write_text("")
    source = make(cfg)
    assert names(source) == ["Other"]
    cfg.write_text(f'[[project]]\nname = "notes"\npath = "{HOME}/notes"\n')
    assert names(source) == ["notes", "Other"]
    # A second edit right after the first can land in the same mtime tick; the size
    # differs, so the (mtime, size, inode) stamp still changes.
    cfg.write_text(f'[[project]]\nname = "renamed"\npath = "{HOME}/notes"\n')
    assert names(source) == ["renamed", "Other"]


def test_config_that_is_a_directory_does_not_crash(tmp_path: Path) -> None:
    cfg = tmp_path / "projects.toml"
    cfg.mkdir()
    source = make(cfg)
    assert source.poll().config_error is not None
    assert not source.can_add


def test_permission_fix_is_picked_up(tmp_path: Path) -> None:
    cfg = tmp_path / "projects.toml"
    cfg.write_text(f'[[project]]\nname = "notes"\npath = "{HOME}/notes"\n')
    cfg.chmod(0)
    try:
        source = make(cfg)
        assert source.poll().config_error is not None
    finally:
        cfg.chmod(0o644)  # chmod changes ctime, not mtime
    assert names(source) == ["notes", "Other"]


def test_add_project_waits_for_a_running_poll(tmp_path: Path) -> None:
    entered, release, added = threading.Event(), threading.Event(), threading.Event()

    def slow(env: Mapping[str, str]) -> list[Pane]:
        entered.set()
        release.wait(5)
        return panes(env)

    source = make(tmp_path / "projects.toml", list_panes=slow)
    poller = threading.Thread(target=source.poll)
    poller.start()
    assert entered.wait(5)
    adder = threading.Thread(
        target=lambda: (source.add_project("notes", HOME / "notes"), added.set())
    )
    adder.start()
    try:
        assert not added.wait(0.3)
    finally:
        release.set()
        poller.join()
        adder.join()
    assert added.is_set()


def test_on_config_runs_after_reloads_only(tmp_path: Path) -> None:
    cfg = tmp_path / "projects.toml"
    cfg.write_text(f'[[project]]\nname = "notes"\npath = "{HOME}/notes"\n')
    seen: list[Config] = []

    def on_config(c: Config) -> list[str]:
        seen.append(c)
        return ["a warning"]

    source = LiveSource(
        cfg,
        env={},
        list_panes=panes,
        sample=sample,
        claude_lookup=lambda p: None,
        git=no_git,
        home=HOME,
        on_config=on_config,
    )
    assert source.poll().config_warnings == ("a warning",)
    source.poll()
    assert len(seen) == 1
    source.regenerate()
    assert len(seen) == 2


def test_on_config_failure_becomes_a_warning(tmp_path: Path) -> None:
    def broken(c: Config) -> list[str]:
        raise OSError("disk full")

    source = LiveSource(
        tmp_path / "p.toml",
        env={},
        list_panes=panes,
        sample=sample,
        claude_lookup=lambda p: None,
        git=no_git,
        home=HOME,
        on_config=broken,
    )
    assert source.poll().config_warnings == ("couldn't update theme files: OSError('disk full')",)


def test_set_theme_existing_and_new(tmp_path: Path) -> None:
    cfg = tmp_path / "projects.toml"
    cfg.write_text(f'[[project]]\nname = "notes"\npath = "{HOME}/notes"\n')
    source = make(cfg)
    source.set_theme(HOME / "notes", "Nord")
    source.set_theme(HOME / "repos/webshop", "Tokyo Night", name="webshop")
    groups = {g.name: g.theme for g in source.poll().groups}
    assert groups == {"notes": "Nord", "webshop": "Tokyo Night"}


def test_accents_come_from_schemes(tmp_path: Path) -> None:
    from cardak.core.themes import Scheme

    cfg = tmp_path / "projects.toml"
    cfg.write_text(f'[[project]]\nname = "notes"\npath = "{HOME}/notes"\ntheme = "Red"\n')
    ansi = ("#000000",) * 8
    red = {"Red": Scheme("Red", "#000000", "#ffffff", "#ff0000", ansi)}
    source = LiveSource(
        cfg,
        env={},
        list_panes=panes,
        sample=sample,
        claude_lookup=lambda p: None,
        git=no_git,
        home=HOME,
        schemes=lambda: red,
    )
    assert source.poll().groups[0].accent == "#ff0000"


class FakeStore:
    def __init__(
        self,
        *states: SessionState,
        broken: bool = False,
        summaries: dict[tuple[str, float], str] | None = None,
        spend: float = 0.0,
        error: str | None = None,
    ) -> None:
        self.states = {s.session_id: s for s in states}
        self.seen: list[tuple[str, float]] = []
        self.broken = broken
        self.summaries = summaries or {}
        self.spend = spend
        self.spend_asked: list[float] = []
        self.error = error

    def latest_error_since(self, ts: float) -> str | None:
        return self.error

    def summaries_for(self, waits: Iterable[tuple[str, float]]) -> dict[str, str]:
        return {sid: self.summaries[(sid, t)] for sid, t in waits if (sid, t) in self.summaries}

    def spend_since(self, ts: float) -> float:
        self.spend_asked.append(ts)
        if self.spend < 0:
            raise StoreError("database is locked")
        return self.spend

    def live_sessions(self) -> list[SessionState]:
        if self.broken:
            raise StoreError("database is locked")
        return list(self.states.values())

    def get(self, session_id: str) -> SessionState | None:
        return self.states.get(session_id)

    def mark_seen(self, session_id: str, now: float) -> None:
        self.seen.append((session_id, now))
        self.states[session_id] = mark_seen(self.states[session_id], now)


def claude_sample(ttys: Iterable[str]) -> dict[str, PaneProcs]:
    return {t: PaneProcs("claude", 1, 50, 1.0, 10, 1, 0.0, 0) for t in ttys}


def agent_source(
    tmp_path: Path, store: FakeStore, history=(), now: float = 102.0, key_file: Path | None = None
) -> LiveSource:
    return LiveSource(
        tmp_path / "projects.toml",
        env={},
        list_panes=panes,
        sample=claude_sample,
        claude_lookup=lambda pid: None,
        git=no_git,
        home=HOME,
        store=store,
        focus_history=lambda: list(history),
        clock=lambda: now,
        key_file=key_file,
    )


WAITING = SessionState("w", pane_id=1, status="waiting", kind="finished", status_since=100.0)


def test_poll_shows_agents(tmp_path: Path) -> None:
    snap = agent_source(tmp_path, FakeStore(WAITING)).poll()
    assert snap.n_waiting == 1 and snap.groups[0].kind == "waiting"


def test_poll_marks_seen_by_focus(tmp_path: Path) -> None:
    store = FakeStore(WAITING)
    snap = agent_source(tmp_path, store, history=[FocusChange(1, 100.2)]).poll()
    assert store.seen == [("w", 102.0)]
    assert snap.n_waiting == 0  # re-read after marking


def test_poll_without_focus_leaves_waiting(tmp_path: Path) -> None:
    store = FakeStore(WAITING)
    agent_source(tmp_path, store, history=[FocusChange(0, 100.2)]).poll()
    assert store.seen == []


def test_mark_seen_pane(tmp_path: Path) -> None:
    other = SessionState("o", pane_id=0, status="waiting", kind="finished")
    working = SessionState("k", pane_id=1, status="working")
    store = FakeStore(WAITING, other, working)
    agent_source(tmp_path, store).mark_seen_pane(1)
    assert store.seen == [("w", 102.0)]


def test_session_lookup(tmp_path: Path) -> None:
    source = agent_source(tmp_path, FakeStore(WAITING))
    assert source.session("w") == WAITING
    assert source.session("nope") is None


def test_store_errors_become_a_warning(tmp_path: Path) -> None:
    snap = agent_source(tmp_path, FakeStore(broken=True)).poll()
    assert snap.n_waiting == 0
    assert any("agent status unavailable" in w for w in snap.config_warnings)


def test_notify_warnings_reach_banner(tmp_path: Path) -> None:
    (tmp_path / "projects.toml").write_text('[notify]\nsound = "nope"\n')
    snap = agent_source(tmp_path, FakeStore()).poll()
    assert any('sound "nope"' in w for w in snap.config_warnings)


def test_no_store_means_no_agents(tmp_path: Path) -> None:
    source = make(tmp_path / "projects.toml")
    assert source.poll().n_waiting == 0
    source.mark_seen_pane(1)
    assert source.session("w") is None


def key(tmp_path: Path, mode: int = 0o600) -> Path:
    path = tmp_path / "anthropic_key"
    path.write_text("k")
    path.chmod(mode)
    return path


def test_source_reads_summaries_for_current_waits(tmp_path: Path) -> None:
    store = FakeStore(WAITING, summaries={("w", 100.0): "Did x; nothing asked", ("w", 5.0): "old"})
    snap = agent_source(tmp_path, store, key_file=key(tmp_path)).poll()
    assert snap.groups[0].panes[0].agent.summary == "Did x; nothing asked"  # type: ignore[union-attr]


def test_source_flags_the_cap(tmp_path: Path) -> None:
    now = datetime(2026, 10, 21, 12).timestamp()
    store = FakeStore(WAITING, spend=5.0)
    assert agent_source(tmp_path, store, now=now, key_file=key(tmp_path)).poll().summary_capped
    assert store.spend_asked[-1] == datetime(2026, 10, 1).timestamp()
    store.spend = 4.99
    assert not agent_source(tmp_path, store, now=now, key_file=key(tmp_path)).poll().summary_capped


def test_source_without_key_file_has_no_summaries(tmp_path: Path) -> None:
    store = FakeStore(WAITING, summaries={("w", 100.0): "S"}, spend=9.0)
    snap = agent_source(tmp_path, store).poll()
    assert snap.groups[0].panes[0].agent.summary is None  # type: ignore[union-attr]
    assert not snap.summary_capped


def test_source_disabled_summaries(tmp_path: Path) -> None:
    (tmp_path / "projects.toml").write_text("[summary]\nmonthly_limit_usd = 0\n")
    store = FakeStore(WAITING, summaries={("w", 100.0): "S"}, spend=9.0)
    source = agent_source(tmp_path, store, key_file=key(tmp_path))
    snap = source.poll()
    assert snap.groups[0].panes[0].agent.summary is None  # type: ignore[union-attr]
    assert not snap.summary_capped
    assert source.summary_info(WAITING) is None


def test_source_warns_about_an_open_key(tmp_path: Path) -> None:
    snap = agent_source(tmp_path, FakeStore(), key_file=key(tmp_path, 0o644)).poll()
    assert (
        "anthropic_key is readable by other users; run chmod 600 ~/.config/cardak/anthropic_key"
        in snap.config_warnings
    )


def test_summary_info(tmp_path: Path) -> None:
    store = FakeStore(WAITING, summaries={("w", 100.0): "Did x; needs y"}, spend=0.42)
    source = agent_source(tmp_path, store, key_file=key(tmp_path))
    assert source.summary_info(WAITING) == SummaryInfo("Did x; needs y", 0.42, 5.0)
    other = SessionState("o", status="waiting", kind="finished", status_since=1.0)
    assert source.summary_info(other) == SummaryInfo(None, 0.42, 5.0)


def test_summary_store_errors(tmp_path: Path) -> None:
    store = FakeStore(WAITING, summaries={("w", 100.0): "S"}, spend=-1.0)
    source = agent_source(tmp_path, store, key_file=key(tmp_path))
    snap = source.poll()
    assert snap.groups[0].panes[0].agent.summary is None  # type: ignore[union-attr]
    assert any("agent status unavailable" in w for w in snap.config_warnings)
    assert source.summary_info(WAITING) is None


def test_source_warns_when_summaries_fail(tmp_path: Path) -> None:
    store = FakeStore(WAITING, error="AuthenticationError: invalid x-api-key")
    source = agent_source(tmp_path, store, key_file=key(tmp_path))
    assert (
        "waiting summaries failing: AuthenticationError: invalid x-api-key; "
        "see ~/.local/state/cardak/hook.log"
    ) in source.poll().config_warnings
    store.error = None
    assert not any("failing" in w for w in source.poll().config_warnings)
