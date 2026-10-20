from pathlib import Path

from cardak.collectors.claude import ClaudeSession
from cardak.collectors.processes import PaneProcs
from cardak.collectors.wezterm import Pane
from cardak.core.config import Config, Project
from cardak.core.resolver import ProjectResolver
from cardak.core.snapshot import build_snapshot, tilde

HOME = Path("/home/user")
R = HOME / "repos"
CONFIG = Config(
    projects=(
        Project(name="webshop", path=R / "webshop", accent="#fabd2f"),
        Project(name="api-gw", path=R / "api-gw"),
    )
)


def pane(
    pane_id: int, cwd: Path | None, tty: str | None, title: str = "zsh", window: int = 0
) -> Pane:
    return Pane(
        pane_id=pane_id,
        window_id=window,
        tab_id=window,
        workspace="default",
        title=title,
        cwd=cwd,
        tty=tty,
        is_active=False,
    )


def procs(
    fg: str = "zsh", cpu: float | None = 1.0, rss: int = 100, claude_pid: int | None = None
) -> PaneProcs:
    return PaneProcs(
        fg_name=fg,
        fg_pid=1,
        claude_pid=claude_pid,
        cpu_pct=cpu,
        rss=rss,
        n_procs=1,
        mcp_cpu_pct=0.0,
        mcp_rss=0,
    )


def fake_git(cwd: Path) -> Path | None:
    return R / "cardak" if cwd.is_relative_to(R / "cardak") else None


def sessions(pid: int) -> ClaudeSession | None:
    return ClaudeSession("s", "webshop-12", R / "webshop") if pid == 77 else None


PANES = [
    pane(3, Path("/tmp"), "/dev/pts/9", title="/tmp", window=2),
    pane(0, R / "cardak", "/dev/pts/5", title="~/repos/cardak"),
    pane(4, R / "webshop" / "src", "/dev/pts/10", title="..epos/webshop"),
    pane(2, R / "api-gw", "/dev/pts/8", title="vim notes.md", window=1),
    pane(5, R / "webshop", "/dev/pts/11", window=3),
    pane(6, HOME, None, window=4),
]
PROCS = {
    "/dev/pts/9": procs(cpu=None),
    "/dev/pts/5": procs(),
    "/dev/pts/10": procs(fg="claude", cpu=290.0, rss=5_000, claude_pid=77),
    "/dev/pts/8": procs(fg="vim", cpu=2.0),
    "/dev/pts/11": procs(fg="claude", claude_pid=78),
}


def snap():
    return build_snapshot(PANES, PROCS, ProjectResolver(CONFIG, git=fake_git), sessions, 0, HOME)


def test_group_order_and_kinds() -> None:
    s = snap()
    assert [(g.kind, g.name) for g in s.groups] == [
        ("config", "webshop"),
        ("config", "api-gw"),
        ("git", "cardak"),
        ("other", "Other"),
    ]
    assert (s.n_projects, s.n_panes) == (3, 6)
    assert s.groups[0].path == "~/repos/webshop"
    assert s.groups[0].accent == "#fabd2f"
    assert s.groups[3].path is None


def test_rows_labels_and_details() -> None:
    webshop, api_gw, cardak, other = snap().groups
    assert [r.label for r in webshop.panes] == ["claude · webshop-12", "claude (new)"]
    assert webshop.panes[0].detail is None  # path-like title dropped
    assert api_gw.panes[0].label == "vim"
    assert api_gw.panes[0].detail == "vim notes.md"
    assert cardak.panes[0].is_self
    assert [r.label for r in other.panes] == ["zsh", "?"]


def test_cwd_display_relative_to_group() -> None:
    webshop, api_gw, cardak, other = snap().groups
    assert [r.cwd_display for r in webshop.panes] == ["src", "."]
    assert [r.cwd_display for r in other.panes] == ["/tmp", "~"]
    assert other.panes[1].cwd == HOME


def test_totals_treat_unknown_cpu_as_zero() -> None:
    webshop, _, _, other = snap().groups
    assert webshop.cpu_pct == 291.0
    assert webshop.rss == 5_100
    assert other.cpu_pct == 0.0
    assert other.panes[1].cpu_pct is None and other.panes[1].rss == 0


def test_claude_session_without_name() -> None:
    def nameless(pid: int) -> ClaudeSession | None:
        return ClaudeSession("s", None, None)

    s = build_snapshot(
        [PANES[4]], PROCS, ProjectResolver(CONFIG, git=fake_git), nameless, None, HOME
    )
    assert s.groups[0].panes[0].label == "claude"


def test_identical_inputs_give_equal_snapshots() -> None:
    assert snap() == snap()


def test_tilde() -> None:
    assert tilde(HOME, HOME) == "~"
    assert tilde(R / "x", HOME) == "~/repos/x"
    assert tilde(Path("/srv/x"), HOME) == "/srv/x"


def test_accent_for_fills_config_groups_only() -> None:
    s = build_snapshot(
        PANES,
        PROCS,
        ProjectResolver(CONFIG, git=fake_git),
        sessions,
        0,
        HOME,
        accent_for=lambda r: "#123456" if r.kind == "config" else "#ffffff",
    )
    assert [g.accent for g in s.groups] == ["#123456", "#123456", None, None]


def test_group_carries_theme() -> None:
    themed = Config(projects=(Project(name="webshop", path=R / "webshop", theme="Nord"),))
    s = build_snapshot(PANES, PROCS, ProjectResolver(themed, git=fake_git), sessions, 0, HOME)
    assert s.groups[0].theme == "Nord"
