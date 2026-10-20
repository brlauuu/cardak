import threading
from collections.abc import Iterable, Mapping
from pathlib import Path

from cardak.collectors.processes import PaneProcs
from cardak.collectors.wezterm import Pane, WeztermUnavailable
from cardak.core.source import LiveSource

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
