import threading
import time
from collections.abc import Iterable, Mapping
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from cardak.collectors.docker import DockerWork
from cardak.collectors.machine import MachineSample
from cardak.collectors.processes import PaneProcs
from cardak.collectors.wezterm import Pane, WeztermUnavailable
from cardak.core.agents import FocusChange, SessionState, mark_seen
from cardak.core.config import Config
from cardak.core.context import ContextSeries, SessionLine
from cardak.core.resources import derive
from cardak.core.snapshot import DeckSnapshot
from cardak.core.source import LiveSource
from cardak.core.spend import MonthSpend
from cardak.core.store import StoreError
from cardak.core.summary import SummaryInfo, month_start
from cardak.core.trends import DayUsage, UsageTrends

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


def no_machine() -> MachineSample | None:
    return None


def no_docker() -> list[DockerWork]:
    return []


def make(cfg: Path, list_panes=panes, env: dict[str, str] | None = None) -> LiveSource:
    return LiveSource(
        cfg,
        env={"WEZTERM_PANE": "1"} if env is None else env,
        list_panes=list_panes,
        sample=sample,
        claude_lookup=lambda pid: None,
        git=no_git,
        home=HOME,
        machine=no_machine,
        docker=no_docker,
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
        machine=no_machine,
        docker=no_docker,
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
        machine=no_machine,
        docker=no_docker,
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
        machine=no_machine,
        docker=no_docker,
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
        self.seen: list[tuple[str, float, float | None]] = []
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

    def mark_seen(self, session_id: str, now: float, wait_since: float | None = None) -> None:
        self.seen.append((session_id, now, wait_since))
        self.states[session_id] = mark_seen(self.states[session_id], now)


def claude_sample(ttys: Iterable[str]) -> dict[str, PaneProcs]:
    return {t: PaneProcs("claude", 1, 50, 1.0, 10, 1, 0.0, 0) for t in ttys}


def agent_source(
    tmp_path: Path,
    store: FakeStore,
    history=(),
    now: float = 102.0,
    keys_dir: Path | None = None,
    write_seen: bool = True,
) -> LiveSource:
    return LiveSource(
        tmp_path / "projects.toml",
        env={},
        list_panes=panes,
        sample=claude_sample,
        claude_lookup=lambda pid: None,
        git=no_git,
        home=HOME,
        machine=no_machine,
        docker=no_docker,
        store=store,
        focus_history=lambda: list(history),
        clock=lambda: now,
        keys_dir=keys_dir,
        write_seen=write_seen,
    )


WAITING = SessionState("w", pane_id=1, status="waiting", kind="finished", status_since=100.0)


def test_poll_shows_agents(tmp_path: Path) -> None:
    snap = agent_source(tmp_path, FakeStore(WAITING)).poll()
    assert snap.n_waiting == 1 and snap.groups[0].kind == "waiting"


def test_poll_marks_seen_by_focus(tmp_path: Path) -> None:
    store = FakeStore(WAITING)
    snap = agent_source(tmp_path, store, history=[FocusChange(1, 100.2)]).poll()
    assert store.seen == [("w", 102.0, 100.0)]  # the wait the deck saw
    assert snap.n_waiting == 0  # re-read after marking


def test_poll_without_focus_leaves_waiting(tmp_path: Path) -> None:
    store = FakeStore(WAITING)
    agent_source(tmp_path, store, history=[FocusChange(0, 100.2)]).poll()
    assert store.seen == []


def pane_agent(snap: DeckSnapshot, pane_id: int):
    return next(
        r.agent for g in snap.groups if g.kind != "waiting" for r in g.panes if r.pane_id == pane_id
    )


class NoWrites(FakeStore):
    def mark_seen(self, session_id: str, now: float, wait_since: float | None = None) -> None:
        raise AssertionError("a read-only source wrote seen_at")


def test_read_only_seen_hides_the_wait_without_writing(tmp_path: Path) -> None:
    store = NoWrites(WAITING)
    source = agent_source(tmp_path, store, history=[FocusChange(1, 100.2)], write_seen=False)
    snap = source.poll()
    assert snap.n_waiting == 0
    assert not any(g.kind == "waiting" for g in snap.groups)
    agent = pane_agent(snap, 1)
    assert agent is not None and agent.status == "idle"
    assert store.states["w"].status == "waiting"  # the store is untouched


def test_read_only_seen_permission_shows_working(tmp_path: Path) -> None:
    asked = SessionState(
        "w", pane_id=1, status="waiting", kind="permission", reason="Bash", status_since=100.0
    )
    source = agent_source(
        tmp_path, NoWrites(asked), history=[FocusChange(1, 100.2)], write_seen=False
    )
    agent = pane_agent(source.poll(), 1)
    assert agent is not None and agent.status == "working"


def test_read_only_seen_age_is_stable(tmp_path: Path) -> None:
    ages = []
    for now in (102.0, 500.0):
        source = agent_source(
            tmp_path, NoWrites(WAITING), history=[FocusChange(1, 100.2)], now=now, write_seen=False
        )
        agent = pane_agent(source.poll(), 1)
        assert agent is not None
        ages.append(agent.since)
    assert ages == [pytest.approx(101.2), pytest.approx(101.2)]  # when it was seen, not each poll


def test_read_only_unseen_wait_stays(tmp_path: Path) -> None:
    source = agent_source(
        tmp_path, NoWrites(WAITING), history=[FocusChange(0, 100.2)], write_seen=False
    )
    assert source.poll().n_waiting == 1


def test_mark_seen_pane(tmp_path: Path) -> None:
    other = SessionState("o", pane_id=0, status="waiting", kind="finished")
    working = SessionState("k", pane_id=1, status="working")
    store = FakeStore(WAITING, other, working)
    agent_source(tmp_path, store).mark_seen_pane(1)
    assert store.seen == [("w", 102.0, 100.0)]


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


def key(tmp_path: Path, mode: int = 0o600, name: str = "anthropic") -> Path:
    """Write a provider key into tmp_path, which is then the deck's keys dir."""
    path = tmp_path / f"{name}_key"
    path.write_text("k")
    path.chmod(mode)
    return tmp_path


def test_source_reads_summaries_for_current_waits(tmp_path: Path) -> None:
    store = FakeStore(WAITING, summaries={("w", 100.0): "Did x; nothing asked", ("w", 5.0): "old"})
    snap = agent_source(tmp_path, store, keys_dir=key(tmp_path)).poll()
    assert snap.groups[0].panes[0].agent.summary == "Did x; nothing asked"  # type: ignore[union-attr]


def test_source_flags_the_cap(tmp_path: Path) -> None:
    now = datetime(2026, 10, 21, 12).timestamp()
    store = FakeStore(WAITING, spend=5.0)
    assert agent_source(tmp_path, store, now=now, keys_dir=key(tmp_path)).poll().summary_capped
    assert store.spend_asked[-1] == datetime(2026, 10, 1).timestamp()
    store.spend = 4.99
    assert not agent_source(tmp_path, store, now=now, keys_dir=key(tmp_path)).poll().summary_capped


def test_source_without_key_file_has_no_summaries(tmp_path: Path) -> None:
    store = FakeStore(WAITING, summaries={("w", 100.0): "S"}, spend=9.0)
    snap = agent_source(tmp_path, store).poll()
    assert snap.groups[0].panes[0].agent.summary is None  # type: ignore[union-attr]
    assert not snap.summary_capped


def test_source_disabled_summaries(tmp_path: Path) -> None:
    (tmp_path / "projects.toml").write_text("[summary]\nmonthly_limit_usd = 0\n")
    store = FakeStore(WAITING, summaries={("w", 100.0): "S"}, spend=9.0)
    source = agent_source(tmp_path, store, keys_dir=key(tmp_path))
    snap = source.poll()
    assert snap.groups[0].panes[0].agent.summary is None  # type: ignore[union-attr]
    assert not snap.summary_capped
    assert source.summary_info(WAITING) is None


def test_source_warns_about_an_open_key(tmp_path: Path) -> None:
    snap = agent_source(tmp_path, FakeStore(), keys_dir=key(tmp_path, 0o644)).poll()
    assert (
        "anthropic_key is readable by other users; run chmod 600 ~/.config/cardak/anthropic_key"
        in snap.config_warnings
    )


def test_source_key_warning_names_active_key(tmp_path: Path) -> None:
    (tmp_path / "projects.toml").write_text(
        '[summary]\nprovider = "fireworks"\n[summary.fireworks]\nmodel = "m"\n'
    )
    key(tmp_path, 0o644)  # anthropic's, not in use
    snap = agent_source(tmp_path, FakeStore(), keys_dir=key(tmp_path, 0o644, "fireworks")).poll()
    assert (
        "fireworks_key is readable by other users; run chmod 600 ~/.config/cardak/fireworks_key"
        in snap.config_warnings
    )
    assert not any(w.startswith("anthropic_key") for w in snap.config_warnings)


def test_source_warns_on_summary_error(tmp_path: Path) -> None:
    (tmp_path / "projects.toml").write_text('[summary]\nprovider = "gpt"\n')
    store = FakeStore(WAITING, summaries={("w", 100.0): "S"})
    snap = agent_source(tmp_path, store, keys_dir=key(tmp_path)).poll()
    assert snap.groups[0].panes[0].agent.summary is None  # type: ignore[union-attr]
    assert any(
        w.startswith("waiting summaries off: [summary] provider 'gpt'")
        for w in snap.config_warnings
    )
    assert snap.config_error is None  # the rest of the file still loads


def test_source_summaries_from_ollama_without_key(tmp_path: Path) -> None:
    (tmp_path / "projects.toml").write_text(
        '[summary]\nprovider = "ollama"\n[summary.ollama]\nmodel = "m"\n'
    )
    store = FakeStore(WAITING, summaries={("w", 100.0): "Did x; nothing asked"})
    snap = agent_source(tmp_path, store, keys_dir=tmp_path).poll()
    assert snap.groups[0].panes[0].agent.summary == "Did x; nothing asked"  # type: ignore[union-attr]


def test_summary_info(tmp_path: Path) -> None:
    store = FakeStore(WAITING, summaries={("w", 100.0): "Did x; needs y"}, spend=0.42)
    source = agent_source(tmp_path, store, keys_dir=key(tmp_path))
    assert source.summary_info(WAITING) == SummaryInfo("Did x; needs y", 0.42, 5.0)
    other = SessionState("o", status="waiting", kind="finished", status_since=1.0)
    assert source.summary_info(other) == SummaryInfo(None, 0.42, 5.0)


def test_summary_store_errors(tmp_path: Path) -> None:
    store = FakeStore(WAITING, summaries={("w", 100.0): "S"}, spend=-1.0)
    source = agent_source(tmp_path, store, keys_dir=key(tmp_path))
    snap = source.poll()
    assert snap.groups[0].panes[0].agent.summary is None  # type: ignore[union-attr]
    assert any("waiting summaries unavailable" in w for w in snap.config_warnings)  # #53
    assert source.summary_info(WAITING) is None


def test_source_warns_when_summaries_fail(tmp_path: Path) -> None:
    store = FakeStore(WAITING, error="AuthenticationError: invalid x-api-key")
    source = agent_source(tmp_path, store, keys_dir=key(tmp_path))
    assert (
        "waiting summaries failing: AuthenticationError: invalid x-api-key; "
        "see ~/.local/state/cardak/hook.log"
    ) in source.poll().config_warnings
    store.error = None
    assert not any("failing" in w for w in source.poll().config_warnings)


GIB = 2**30
WEBSHOP_CFG = f'[[project]]\nname = "webshop"\npath = "{HOME}/repos/webshop"\n'


def machine_20_used() -> MachineSample:
    return MachineSample(32 * GIB, 12 * GIB, GIB, GIB, 25.0, 16)


def resourced(cfg: Path, machine=machine_20_used, docker=no_docker, list_panes=panes) -> LiveSource:
    return LiveSource(
        cfg,
        env={},
        list_panes=list_panes,
        sample=sample,
        claude_lookup=lambda pid: None,
        git=no_git,
        home=HOME,
        machine=machine,
        docker=docker,
    )


def test_poll_attaches_docker_and_machine(tmp_path: Path) -> None:
    cfg = tmp_path / "projects.toml"
    cfg.write_text(WEBSHOP_CFG)
    db = DockerWork("container", "i", "webshop-db-1", GIB, 5.0, HOME / "repos/webshop")
    snap = resourced(cfg, docker=lambda: [db]).poll()
    webshop, other = snap.groups
    assert [line.label for line in webshop.docker] == ["docker: webshop-db-1"]
    assert other.kind == "other" and other.outside_rss is not None
    assert snap.machine_view is not None and snap.machine_view.ram_spark != ""
    assert snap.machine is not None
    assert snap.thresholds == derive(
        Config().resources, snap.machine.mem_total, snap.machine.n_cpus
    )


def test_unreachable_wezterm_still_shows_machine(tmp_path: Path) -> None:
    snap = resourced(tmp_path / "p.toml", list_panes=unavailable).poll()
    assert snap.error == "no WezTerm GUI socket found"
    assert snap.machine_view is not None and snap.groups == ()


def test_sampler_errors_do_not_break_poll(tmp_path: Path) -> None:
    def broken_machine() -> MachineSample | None:
        raise OSError("boom")

    def broken_docker() -> list[DockerWork]:
        raise RuntimeError("boom")

    snap = resourced(tmp_path / "p.toml", machine=broken_machine, docker=broken_docker).poll()
    assert [g.name for g in snap.groups] == ["Other"]
    assert snap.machine_view is None


def test_resources_setting_changes_marks(tmp_path: Path) -> None:
    cfg = tmp_path / "projects.toml"
    cfg.write_text(WEBSHOP_CFG + "\n[resources]\npane_cpu_pct = 0.5\n")
    webshop = resourced(cfg).poll().groups[0]
    assert webshop.over and webshop.panes[0].over


def test_default_thresholds_leave_small_panes_unmarked(tmp_path: Path) -> None:
    cfg = tmp_path / "projects.toml"
    cfg.write_text(WEBSHOP_CFG)
    webshop = resourced(cfg).poll().groups[0]
    assert not webshop.over and webshop.ram_spark != ""


def test_summaries_failure_message(tmp_path: Path) -> None:
    store = FakeStore(WAITING, spend=-1.0)  # spend_since raises StoreError
    snap = agent_source(tmp_path, store, keys_dir=key(tmp_path)).poll()
    assert any(w.startswith("waiting summaries unavailable:") for w in snap.config_warnings)
    assert not any("agent status unavailable" in w for w in snap.config_warnings)


def test_no_summary_info_without_key(tmp_path: Path) -> None:
    store = FakeStore(WAITING, spend=1.0)
    source = agent_source(tmp_path, store, keys_dir=tmp_path)  # no key there
    assert source.summary_info(WAITING) is None
    assert source.poll().summary_capped is False


def spending(cfg: Path, spend: MonthSpend | None, list_panes=panes, git=no_git) -> LiveSource:
    return LiveSource(
        cfg,
        env={"WEZTERM_PANE": "1"},
        list_panes=list_panes,
        sample=sample,
        claude_lookup=lambda pid: None,
        git=git,
        home=HOME,
        machine=no_machine,
        docker=no_docker,
        spend=lambda: spend,
    )


WEBSHOP_CFG = f'[[project]]\nname = "webshop"\npath = "{HOME}/repos/webshop"\n'
THIS_MONTH = month_start(time.time())
MONTH = MonthSpend(
    THIS_MONTH,
    60.0,
    {
        f"{HOME}/repos/webshop": 10.0,
        f"{HOME}/repos/webshop/api": 5.0,
        f"{HOME}/repos/api-gw": 30.0,  # a git root with no pane open
        f"{HOME}/notes": 7.0,
        None: 8.0,
    },
    (),
)


def git_api_gw(cwd: Path) -> Path | None:
    return HOME / "repos/api-gw" if cwd.is_relative_to(HOME / "repos/api-gw") else None


def test_spend_by_project(tmp_path: Path) -> None:
    cfg = tmp_path / "p.toml"
    cfg.write_text(WEBSHOP_CFG)
    snap = spending(cfg, MONTH, git=git_api_gw).poll()
    assert snap.month_spend == 60.0  # api-gw's 30 is in the total, though it has no row
    spent = {g.name: g.spend_usd for g in snap.groups}
    assert spent == {"webshop": 15.0, "Other": 15.0}  # notes and no cwd both resolve to Other


def test_spend_none_before_first_pass(tmp_path: Path) -> None:
    snap = spending(tmp_path / "p.toml", None).poll()
    assert snap.month_spend is None
    assert all(g.spend_usd is None for g in snap.groups)


def test_spend_left_off_groups_without_any(tmp_path: Path) -> None:
    cfg = tmp_path / "p.toml"
    cfg.write_text(WEBSHOP_CFG)
    only_webshop = MonthSpend(THIS_MONTH, 1.0, {f"{HOME}/repos/webshop": 1.0}, ())
    spent = {g.name: g.spend_usd for g in spending(cfg, only_webshop).poll().groups}
    assert spent == {"webshop": 1.0, "Other": None}


def test_spend_never_on_waiting_group(tmp_path: Path) -> None:
    source = agent_source(tmp_path, FakeStore(WAITING))
    source._spend = lambda: replace(MONTH, since=month_start(102.0))  # its clock
    snap = source.poll()
    assert [(g.kind, g.spend_usd) for g in snap.groups] == [("waiting", None), ("other", 60.0)]


def test_spend_follows_config_change(tmp_path: Path) -> None:
    cfg = tmp_path / "p.toml"
    cfg.write_text("")
    source = spending(cfg, MONTH)
    assert {g.name: g.spend_usd for g in source.poll().groups} == {"Other": 60.0}
    source.add_project("webshop", HOME / "repos/webshop")
    assert {g.name: g.spend_usd for g in source.poll().groups} == {"webshop": 15.0, "Other": 45.0}


def test_spend_with_wezterm_down(tmp_path: Path) -> None:
    snap = spending(tmp_path / "p.toml", MONTH, list_panes=unavailable).poll()
    assert snap.error is not None
    assert snap.month_spend == 60.0


def test_unpriced_warning(tmp_path: Path) -> None:
    month = MonthSpend(THIS_MONTH, 1.0, {}, ("claude-opus-5-5 (fast mode)", "claude-x"))
    snap = spending(tmp_path / "p.toml", month).poll()
    assert snap.spend_unpriced == ("claude-opus-5-5 (fast mode)", "claude-x")
    assert (
        "no API price for claude-opus-5-5 (fast mode), claude-x; left out of this month's figure"
        in snap.config_warnings
    )


def test_spend_from_last_month_is_not_shown(tmp_path: Path) -> None:
    # a counter that keeps failing past the 1st must not show last month's figure as this month's
    now = datetime(2026, 11, 1, 9, 0).timestamp()
    october = MonthSpend(datetime(2026, 10, 1).timestamp(), 899.0, {None: 899.0}, ())
    source = spending(tmp_path / "p.toml", october)
    source._clock = lambda: now
    snap = source.poll()
    assert snap.month_spend is None
    assert all(g.spend_usd is None for g in snap.groups)


def trending(today: date) -> MonthSpend:
    cells: dict[tuple[str | None, int], DayUsage] = {
        (f"{HOME}/repos/webshop", 29): DayUsage(replies=1, prompts=1, prompt_chars=5)
    }
    return replace(MONTH, trends=UsageTrends(today, cells, ()))


def test_snapshot_carries_grouped_trends(tmp_path: Path) -> None:
    cfg = tmp_path / "p.toml"
    cfg.write_text(WEBSHOP_CFG)
    snap = spending(cfg, trending(date.today())).poll()
    assert snap.usage_trends is not None
    assert [r.name for r in snap.usage_trends.rows] == ["webshop", "All projects"]


def test_trends_from_yesterday_are_not_shown(tmp_path: Path) -> None:
    snap = spending(tmp_path / "p.toml", trending(date.today() - timedelta(days=1))).poll()
    assert snap.usage_trends is None
    assert snap.month_spend == 60.0


def test_grouped_trends_are_cached(tmp_path: Path) -> None:
    source = spending(tmp_path / "p.toml", trending(date.today()))
    first, second = source.poll(), source.poll()
    assert first.usage_trends is not None and first.usage_trends is second.usage_trends


def test_broken_sound_pack_becomes_a_warning(tmp_path: Path, packs_dir: Path) -> None:
    # the deck reads the test's own packs folder, never the user's (#91)
    (packs_dir / "bad").mkdir()
    (packs_dir / "bad" / "openpeon.json").write_text("{")
    source = LiveSource(
        tmp_path / "p.toml",
        env={},
        list_panes=panes,
        sample=sample,
        claude_lookup=lambda p: None,
        git=no_git,
        home=HOME,
        machine=no_machine,
        docker=no_docker,
    )
    (warning,) = source.poll().config_warnings
    assert warning.startswith("sound pack bad: openpeon.json unreadable")


def with_context(today: date, latest: dict[str, int]) -> MonthSpend:
    line = SessionLine("w", f"{HOME}/repos/webshop", ((time.time() - 60, 120_000),))
    return replace(trending(today), context=ContextSeries(today, time.time(), (line,), latest))


def test_snapshot_carries_grouped_context(tmp_path: Path) -> None:
    cfg = tmp_path / "p.toml"
    cfg.write_text(WEBSHOP_CFG)
    snap = spending(cfg, with_context(date.today(), {})).poll()
    assert snap.usage_context is not None
    assert [r.name for r in snap.usage_context.rows] == ["webshop", "All projects"]


def test_context_from_yesterday_is_not_shown(tmp_path: Path) -> None:
    snap = spending(tmp_path / "p.toml", with_context(date.today() - timedelta(days=1), {})).poll()
    assert snap.usage_context is None


def test_grouped_context_is_cached(tmp_path: Path) -> None:
    source = spending(tmp_path / "p.toml", with_context(date.today(), {}))
    first, second = source.poll(), source.poll()
    assert first.usage_context is not None and first.usage_context is second.usage_context


def test_agent_rows_carry_their_context(tmp_path: Path) -> None:
    source = agent_source(tmp_path, FakeStore(WAITING))
    month = with_context(date.fromtimestamp(102.0), {"w": 312_000})
    source._spend = lambda: replace(month, since=month_start(102.0))
    snap = source.poll()
    agents = [p.agent for g in snap.groups for p in g.panes if p.agent is not None]
    assert agents and all(a.context == 312_000 for a in agents)  # the waiting row too


def test_agent_rows_without_a_reply_have_no_context(tmp_path: Path) -> None:
    source = agent_source(tmp_path, FakeStore(WAITING))
    month = with_context(date.fromtimestamp(102.0), {})
    source._spend = lambda: replace(month, since=month_start(102.0))
    agents = [p.agent for g in source.poll().groups for p in g.panes if p.agent is not None]
    assert agents and all(a.context is None for a in agents)


def test_source_reads_limits_when_changed(tmp_path: Path) -> None:
    from cardak.collectors.limits import Reading, Window
    from cardak.core.limits import record

    path = tmp_path / "limits.json"
    source = spending(tmp_path / "p.toml", None)
    source._limits_file = path
    assert source.poll().plan_limits is None  # no file: no row
    future = time.time() + 3600
    record(path, Reading(Window(73.0, future), None), time.time())
    view = source.poll().plan_limits
    assert view is not None and view.five_hour.left == 27 and view.seven_day.left is None
    record(path, Reading(Window(95.0, future), None), time.time())
    view = source.poll().plan_limits
    assert view is not None and view.five_hour.left == 5 and view.five_hour.level == "critical"
    path.unlink()
    assert source.poll().plan_limits is None


def test_source_without_limits_file_reads_none(tmp_path: Path) -> None:
    assert spending(tmp_path / "p.toml", None).poll().plan_limits is None
