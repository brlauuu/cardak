import threading
import time
from dataclasses import replace
from pathlib import Path

from logfix import FakeLog
from textual.pilot import Pilot
from textual.widgets import Input, Static, Tree

from cardak.collectors.machine import MachineSample
from cardak.core.agents import SessionState
from cardak.core.config import ConfigError
from cardak.core.navigate import JumpResult
from cardak.core.resolver import Kind
from cardak.core.snapshot import (
    WAITING_NAME,
    AgentView,
    DeckSnapshot,
    DockerLine,
    Group,
    MachineView,
    PaneRow,
)
from cardak.core.store import ResourceEvent
from cardak.core.summary import SummaryInfo
from cardak.core.themes import Scheme, SchemeStore
from cardak.ui.app import AddProjectScreen, DeckApp, Node, node_id
from cardak.ui.detail import DetailScreen, TranscriptScreen
from cardak.ui.theme_picker import ThemePickerScreen


def row(pane_id: int, cwd: Path | None = None) -> PaneRow:
    return PaneRow(
        pane_id=pane_id,
        label="zsh",
        detail=None,
        cwd=cwd,
        cwd_display=".",
        cpu_pct=1.0,
        rss=2**20,
        is_self=False,
    )


def group(kind: Kind, name: str, key: Path | None, *rows: PaneRow) -> Group:
    return Group(
        kind=kind,
        key=key,
        name=name,
        accent=None,
        path=str(key) if key else None,
        cpu_pct=1.0,
        rss=2**20,
        panes=rows,
    )


SNAP = DeckSnapshot(
    groups=(
        group("config", "webshop", Path("/repos/webshop"), row(1)),
        group("git", "cardak", Path("/repos/cardak"), row(2), row(3)),
        group("other", "Other", None, row(4, cwd=Path("/tmp"))),
    ),
    n_panes=4,
)


class StubSource:
    def __init__(self, *snaps: DeckSnapshot, can_add: bool = True, fail_add: bool = False) -> None:
        self.snaps = list(snaps)
        self.can_add = can_add
        self.fail_add = fail_add
        self.added: list[tuple[str, Path]] = []
        self.themes: list[tuple[Path, str, str | None]] = []
        self.regenerated = 0
        self.seen_panes: list[int] = []
        self.sessions: dict[str, SessionState] = {}
        self.info: SummaryInfo | None = None

    def poll(self) -> DeckSnapshot:
        return self.snaps.pop(0) if len(self.snaps) > 1 else self.snaps[0]

    def add_project(self, name: str, path: Path) -> None:
        if self.fail_add:
            raise ConfigError(Path("projects.toml"), f"{path} is already configured")
        self.added.append((name, path))

    def set_theme(self, project_path: Path, theme: str, name: str | None = None) -> None:
        self.themes.append((project_path, theme, name))

    def regenerate(self) -> None:
        self.regenerated += 1

    def mark_seen_pane(self, pane_id: int) -> None:
        self.seen_panes.append(pane_id)

    def session(self, session_id: str) -> SessionState | None:
        return self.sessions.get(session_id)

    def summary_info(self, state: SessionState) -> SummaryInfo | None:
        return self.info


class BrokenSource(StubSource):
    def poll(self) -> DeckSnapshot:
        raise RuntimeError("boom")


async def settle(app: DeckApp, pilot: Pilot[None]) -> None:
    await app.workers.wait_for_complete()
    await pilot.pause()


def labels(app: DeckApp) -> list[str]:
    return [str(node.label) for node in app.deck_tree.root.children]


async def test_renders_groups_and_counts() -> None:
    app = DeckApp(StubSource(SNAP), interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        assert [label.split()[0] for label in labels(app)] == ["webshop", "cardak", "Other"]
        assert app.sub_title == "2 projects · 4 panes"
        assert len(app.deck_tree.root.children[1].children) == 2


async def test_collapse_survives_refresh() -> None:
    second = replace(SNAP, n_panes=5)
    app = DeckApp(StubSource(SNAP, second), interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.deck_tree.root.children[1].collapse()
        await pilot.pause()
        app.action_refresh()
        await settle(app, pilot)
        assert app.snapshot == second
        assert [n.is_expanded for n in app.deck_tree.root.children] == [True, False, True]


async def test_cursor_survives_refresh() -> None:
    second = replace(SNAP, n_panes=5)
    app = DeckApp(StubSource(SNAP, second), interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.deck_tree.move_cursor(app.deck_tree.root.children[1].children[1])
        app.action_refresh()
        await settle(app, pilot)
        cursor = app.deck_tree.cursor_node
        assert cursor is not None and cursor.data is not None
        assert cursor.data.row is not None and cursor.data.row.pane_id == 3


async def test_unreachable_message() -> None:
    app = DeckApp(StubSource(DeckSnapshot(error="no WezTerm GUI socket found")), interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        assert labels(app) == ["WezTerm not reachable: no WezTerm GUI socket found · retrying"]


async def test_poll_exception_shows_internal_error() -> None:
    app = DeckApp(BrokenSource(SNAP), interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        assert labels(app)[0] == "cardak hit an internal error: RuntimeError('boom') · retrying"
        assert "WezTerm" not in labels(app)[0]  # not blamed on WezTerm (#12)
        assert app.is_running


async def test_config_error_banner() -> None:
    broken = replace(SNAP, config_error="projects.toml line 2: Invalid value")
    app = DeckApp(StubSource(broken, SNAP), interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        assert app.query_one("#banner", Static).display is True
        app.action_refresh()
        await settle(app, pilot)
        assert app.query_one("#banner", Static).display is False


async def test_add_unconfigured_git_group() -> None:
    source = StubSource(SNAP)
    app = DeckApp(source, interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.deck_tree.move_cursor(app.deck_tree.root.children[1])
        await pilot.press("a")
        await pilot.pause()
        assert isinstance(app.screen, AddProjectScreen)
        assert app.screen.query_one(Input).value == "cardak"
        await pilot.press("enter")
        await settle(app, pilot)
        assert source.added == [("cardak", Path("/repos/cardak"))]


async def test_add_pane_in_other_uses_its_cwd_and_escape_cancels() -> None:
    source = StubSource(SNAP)
    app = DeckApp(source, interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.deck_tree.move_cursor(app.deck_tree.root.children[2].children[0])
        await pilot.press("a")
        await pilot.pause()
        assert isinstance(app.screen, AddProjectScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert source.added == []
        await pilot.press("a")
        await pilot.pause()
        await pilot.press("enter")
        await settle(app, pilot)
        assert source.added == [("tmp", Path("/tmp"))]


async def test_add_refused_on_configured_group_and_broken_config() -> None:
    for source in (StubSource(SNAP), StubSource(SNAP, can_add=False)):
        app = DeckApp(source, interval=3600)
        async with app.run_test() as pilot:
            await settle(app, pilot)
            target = 0 if source.can_add else 1
            app.deck_tree.move_cursor(app.deck_tree.root.children[target])
            await pilot.press("a")
            await pilot.pause()
            assert not isinstance(app.screen, AddProjectScreen)
            assert source.added == []


async def test_add_failure_is_reported_not_raised() -> None:
    source = StubSource(SNAP, fail_add=True)
    app = DeckApp(source, interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.deck_tree.move_cursor(app.deck_tree.root.children[1])
        await pilot.press("a")
        await pilot.pause()
        await pilot.press("enter")
        await settle(app, pilot)
        assert app.is_running
        assert source.added == []


class SlowSource(StubSource):
    def __init__(self, *snaps: DeckSnapshot) -> None:
        super().__init__(*snaps)
        self.release = threading.Event()
        self.calls = 0

    def poll(self) -> DeckSnapshot:
        self.calls += 1
        self.release.wait(5)
        return super().poll()


async def test_refresh_during_a_poll_does_not_start_another() -> None:
    source = SlowSource(SNAP)
    app = DeckApp(source, interval=3600)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.action_refresh()
        app.action_refresh()
        await pilot.pause()
        assert source.calls == 1
        source.release.set()
        await settle(app, pilot)


async def test_markup_in_names_and_paths_is_shown_literally() -> None:
    odd = Path("/repos/odd[bold]")
    snap = replace(SNAP, groups=(group("git", "odd[bold]", odd, row(9)),), n_panes=1)
    source = StubSource(snap)
    app = DeckApp(source, interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.deck_tree.move_cursor(app.deck_tree.root.children[0])
        await pilot.press("a")
        await pilot.pause()
        assert isinstance(app.screen, AddProjectScreen)
        assert app.screen.query_one(Input).value == "odd[bold]"
        app.screen.query_one(Input).value = "foo[/b]"  # unbalanced closing tag
        await pilot.press("enter")
        await settle(app, pilot)
        assert app.is_running
        assert source.added == [("foo[/b]", odd)]


async def test_config_error_with_markup_is_shown_literally() -> None:
    broken = replace(SNAP, config_error="projects.toml: cannot read: [Errno 13] x [/b] y")
    app = DeckApp(StubSource(broken), interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.pause()
        assert app.is_running
        assert app.query_one("#banner", Static).display is True


RAISED = JumpResult("raised")


class StubNavigator:
    def __init__(self, result: JumpResult = RAISED) -> None:
        self.result = result
        self.jumps: list[int] = []

    def jump(self, pane_id: int) -> JumpResult:
        self.jumps.append(pane_id)
        return self.result


async def test_enter_on_pane_jumps() -> None:
    nav = StubNavigator()
    app = DeckApp(StubSource(SNAP), navigator=nav, interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.deck_tree.move_cursor(app.deck_tree.root.children[1].children[1])
        await pilot.press("enter")
        await settle(app, pilot)
        assert nav.jumps == [3]


async def test_enter_on_group_toggles_without_jumping() -> None:
    nav = StubNavigator()
    app = DeckApp(StubSource(SNAP), navigator=nav, interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.deck_tree.move_cursor(app.deck_tree.root.children[0])
        await pilot.press("enter")
        await settle(app, pilot)
        assert nav.jumps == []
        assert not app.deck_tree.root.children[0].is_expanded


async def test_jump_failure_is_notified() -> None:
    nav = StubNavigator(JumpResult("failed", "Couldn't jump: [/b] gone"))
    app = DeckApp(StubSource(SNAP), navigator=nav, interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.jump(1)
        await settle(app, pilot)
        await pilot.pause()
        assert app.is_running
        assert [n.message for n in app._notifications] == [r"Couldn't jump: \[/b] gone"]


async def test_j_and_k_move_the_cursor() -> None:
    app = DeckApp(StubSource(SNAP), interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.deck_tree.move_cursor(app.deck_tree.root.children[0])
        await pilot.pause()
        await pilot.press("j", "j")
        assert app.deck_tree.cursor_line == 2
        await pilot.press("k")
        assert app.deck_tree.cursor_line == 1


async def open_find(app: DeckApp, pilot: Pilot[None], *keys: str) -> None:
    await pilot.press("slash")
    await pilot.pause()
    await pilot.press(*keys)
    await pilot.pause()


async def test_find_filters_and_enter_jumps_to_best() -> None:
    nav = StubNavigator()
    app = DeckApp(StubSource(SNAP), navigator=nav, interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await open_find(app, pilot, "w", "e", "b")
        assert app.find_query == "web"
        assert [label.split()[0] for label in labels(app)] == ["webshop"]
        assert app.sub_title == '1 of 4 panes match "web"'
        await pilot.press("enter")
        await settle(app, pilot)
        assert nav.jumps == [1]


async def test_escape_restores_the_full_tree() -> None:
    app = DeckApp(StubSource(SNAP), interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await open_find(app, pilot, "w", "e", "b")
        await pilot.press("escape")
        await pilot.pause()
        assert app.find_query == ""
        assert len(labels(app)) == 3
        assert app.query_one("#find", Input).display is False
        assert app.focused is app.deck_tree
        assert app.sub_title == "2 projects · 4 panes"


async def test_filter_survives_a_refresh() -> None:
    second = replace(SNAP, n_panes=5)
    app = DeckApp(StubSource(SNAP, second), interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await open_find(app, pilot, "w", "e", "b")
        app.action_refresh()
        await settle(app, pilot)
        assert app.raw == second
        assert [label.split()[0] for label in labels(app)] == ["webshop"]


async def test_no_match_shows_a_message_and_enter_does_nothing() -> None:
    nav = StubNavigator()
    app = DeckApp(StubSource(SNAP), navigator=nav, interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await open_find(app, pilot, "z", "z", "z")
        assert labels(app) == ['No pane matches "zzz"']
        await pilot.press("enter")
        await settle(app, pilot)
        assert nav.jumps == []


async def test_typing_j_k_into_find_does_not_move_cursor() -> None:
    app = DeckApp(StubSource(SNAP), interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        line = app.deck_tree.cursor_line
        await open_find(app, pilot, "j", "k", "slash")
        assert app.query_one("#find", Input).value == "jk/"
        assert app.deck_tree.cursor_line == line


async def test_find_query_with_markup_characters() -> None:
    app = DeckApp(StubSource(SNAP), interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await open_find(app, pilot, "left_square_bracket", "slash", "b", "right_square_bracket")
        await pilot.pause()
        assert app.is_running
        assert labels(app) == ['No pane matches "[/b]"']


async def test_best_match_in_collapsed_group_selects_group() -> None:
    app = DeckApp(StubSource(SNAP), interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.deck_tree.root.children[0].collapse()
        await pilot.pause()
        await open_find(app, pilot, "w", "e", "b")
        cursor = app.deck_tree.cursor_node
        assert cursor is not None and cursor.data is not None
        assert cursor.data.row is None and cursor.data.group.name == "webshop"


async def test_cursor_starts_on_the_first_row() -> None:
    nav = StubNavigator()
    app = DeckApp(StubSource(SNAP), navigator=nav, interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.pause()
        assert app.deck_tree.cursor_line == 0
        await pilot.press("j", "enter")
        await settle(app, pilot)
        assert nav.jumps == [1]


THEMES = {"Nord": Scheme("Nord", "#101010", "#e0e0e0", None, ("#000000",) * 8)}


async def test_t_on_a_git_group_saves_a_theme() -> None:
    source = StubSource(SNAP)
    app = DeckApp(source, interval=3600, schemes=SchemeStore(THEMES))
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.deck_tree.move_cursor(app.deck_tree.root.children[1])
        await pilot.press("t")
        await settle(app, pilot)
        assert isinstance(app.screen, ThemePickerScreen)
        await pilot.press("enter")
        await settle(app, pilot)
        assert source.themes == [(Path("/repos/cardak"), "Nord", "cardak")]


async def test_t_needs_a_project_and_a_valid_config() -> None:
    for source, row in ((StubSource(SNAP), 2), (StubSource(SNAP, can_add=False), 0)):
        app = DeckApp(source, interval=3600, schemes=SchemeStore(THEMES))
        async with app.run_test() as pilot:
            await settle(app, pilot)
            app.deck_tree.move_cursor(app.deck_tree.root.children[row])
            await pilot.press("t")
            await pilot.pause()
            assert not isinstance(app.screen, ThemePickerScreen)


async def test_startup_loads_schemes_and_regenerates() -> None:
    source = StubSource(SNAP)
    store = SchemeStore()
    app = DeckApp(source, interval=3600, schemes=store, load_schemes=lambda: THEMES)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await settle(app, pilot)
        assert store.get() == THEMES
        assert source.regenerated == 1


async def test_warnings_show_in_the_banner() -> None:
    warned = replace(SNAP, config_warnings=('projects.toml: unknown scheme "Nrod" for typo',))
    app = DeckApp(StubSource(warned), interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        banner = app.query_one("#banner", Static)
        assert banner.display is True
        assert banner.has_class("warning")


async def test_saving_a_theme_says_how_to_enable_themes() -> None:
    source = StubSource(SNAP)
    app = DeckApp(
        source, interval=3600, schemes=SchemeStore(THEMES), theme_status=lambda: "install it"
    )
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.deck_tree.move_cursor(app.deck_tree.root.children[1])
        await pilot.press("t")
        await settle(app, pilot)
        await pilot.press("enter")
        await settle(app, pilot)
        assert "install it" in [n.message for n in app._notifications]


def agent_row(pane_id: int, status: str, kind: str | None = None, since: float = 0.0) -> PaneRow:
    agent = AgentView(f"s{pane_id}", status, kind, since, "Merge them?", None, "webshop")  # type: ignore[arg-type]
    return replace(row(pane_id), label="claude", is_claude=True, agent=agent)


def waiting_snap(*waiting_rows: PaneRow, others: tuple[PaneRow, ...] = ()) -> DeckSnapshot:
    project = group("config", "webshop", Path("/repos/webshop"), *waiting_rows, *others)
    waiting = Group(
        kind="waiting", key=None, name=WAITING_NAME, accent=None, path=None,
        cpu_pct=0.0, rss=0, panes=waiting_rows,
    )  # fmt: skip
    groups = (waiting, project) if waiting_rows else (project,)
    return DeckSnapshot(
        groups=groups, n_panes=len(waiting_rows) + len(others), n_waiting=len(waiting_rows)
    )


def all_nodes(app: DeckApp) -> list:
    out, todo = [], list(app.deck_tree.root.children)
    while todo:
        node = todo.pop(0)
        out.append(node)
        todo[0:0] = list(node.children)
    return out


async def test_waiting_rows_have_their_own_node_ids() -> None:
    snap = waiting_snap(agent_row(7, "waiting", "question"))
    nav = StubNavigator()
    source = StubSource(snap)
    app = DeckApp(source, navigator=nav, interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        ids = [node_id(n.data) for n in all_nodes(app)]
        assert ids == ["g:waiting:None", "w:7", "g:config:/repos/webshop", "p:7", "r:7"]
        for target in (1, 4):  # the waiting row and the reason line
            app.deck_tree.move_cursor(all_nodes(app)[target])
            await pilot.press("enter")
            await settle(app, pilot)
        assert nav.jumps == [7, 7]
        app.deck_tree.root.children[0].collapse()
        await pilot.pause()
        app.action_refresh()
        await settle(app, pilot)
        assert not app.deck_tree.root.children[0].is_expanded


async def test_waiting_row_has_reason_line() -> None:
    snap = waiting_snap(
        agent_row(7, "waiting", "question"), others=(agent_row(8, "working"), agent_row(9, "idle"))
    )
    app = DeckApp(StubSource(snap), interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        project = app.deck_tree.root.children[1]
        roles = [
            (n.data.role, n.data.row.pane_id)
            for n in project.children
            if n.data is not None and n.data.row is not None
        ]
        assert roles == [("pane", 7), ("reason", 7), ("pane", 8), ("pane", 9)]
        assert "Your answer: Merge them?" in str(project.children[1].label)
        assert str(app.deck_tree.root.children[0].label) == WAITING_NAME


async def test_w_toggles_waiting_only() -> None:
    snap = waiting_snap(agent_row(7, "waiting", "finished"), others=(row(2),))
    app = DeckApp(StubSource(snap), interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("w")
        await pilot.pause()
        assert [str(n.label) for n in app.deck_tree.root.children] == [WAITING_NAME]
        await pilot.press("w")
        await pilot.pause()
        assert len(app.deck_tree.root.children) == 2


async def test_w_with_nobody_waiting() -> None:
    app = DeckApp(StubSource(SNAP), interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("w")
        await pilot.pause()
        assert labels(app) == ["Nobody is waiting on you."]


async def test_header_counts_waiting() -> None:
    snap = waiting_snap(agent_row(7, "waiting", "finished"), agent_row(8, "waiting", "question"))
    app = DeckApp(StubSource(snap), interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        assert app.sub_title.endswith("· 2 waiting")


async def test_header_spend() -> None:
    for spent, unpriced, ending in (
        (412.0, (), " · $412 this month at API prices"),
        (412.0, ("claude-x",), " · $412+ this month at API prices"),
        (0.0, (), " · $0 this month at API prices"),
    ):
        snap = replace(SNAP, month_spend=spent, spend_unpriced=unpriced)
        app = DeckApp(StubSource(snap), interval=3600)
        async with app.run_test() as pilot:
            await settle(app, pilot)
            assert app.sub_title.endswith(ending)
    app = DeckApp(StubSource(SNAP), interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        assert "this month" not in app.sub_title


async def test_header_spend_while_filtering() -> None:
    app = DeckApp(StubSource(replace(SNAP, month_spend=37.0)), interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await open_find(app, pilot, "w", "e", "b")
        assert app.sub_title == '1 of 4 panes match "web" · $37 this month at API prices'


async def test_jump_marks_seen() -> None:
    snap = waiting_snap(agent_row(7, "waiting", "finished"))
    for result, expected in ((RAISED, [7]), (JumpResult("failed", "no"), [])):
        source = StubSource(snap)
        app = DeckApp(source, navigator=StubNavigator(result), interval=3600)
        async with app.run_test() as pilot:
            await settle(app, pilot)
            app.jump(7)
            await settle(app, pilot)
            assert source.seen_panes == expected


async def test_hooks_hint_once() -> None:
    calls: list[int] = []

    def hint() -> str:
        calls.append(1)
        return "Run `cardak hooks install` for agent status"

    app = DeckApp(StubSource(SNAP), interval=3600, hooks_status=hint)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.action_refresh()
        await settle(app, pilot)
        assert calls == [1]
        assert any("hooks install" in str(n.message) for n in app._notifications)  # noqa: SLF001


def test_node_id_none() -> None:
    assert node_id(None) is None
    assert node_id(Node(SNAP.groups[0])) == "g:config:/repos/webshop"


async def test_i_opens_detail_on_claude_rows_only() -> None:
    snap = waiting_snap(agent_row(7, "waiting", "finished"), others=(row(2),))
    source = StubSource(snap)
    source.sessions["s7"] = SessionState("s7", pane_id=7, status="waiting", kind="finished")
    nav = StubNavigator()
    app = DeckApp(source, navigator=nav, interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        project = app.deck_tree.root.children[1]
        app.deck_tree.move_cursor(project.children[2])  # the shell pane
        await pilot.press("i")
        await pilot.pause()
        assert not isinstance(app.screen, DetailScreen)
        app.deck_tree.move_cursor(app.deck_tree.root.children[0].children[0])
        await pilot.press("i")
        await pilot.pause()
        assert isinstance(app.screen, DetailScreen)
        await pilot.press("enter")
        await settle(app, pilot)
        assert nav.jumps == [7]


async def test_i_without_a_record_says_so() -> None:
    snap = waiting_snap(agent_row(7, "waiting", "finished"))
    app = DeckApp(StubSource(snap), interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.deck_tree.move_cursor(app.deck_tree.root.children[0].children[0])
        await pilot.press("i")
        await pilot.pause()
        assert any("No status" in str(n.message) for n in app._notifications)  # noqa: SLF001


async def test_deck_keys_do_nothing_on_other_screens() -> None:
    # live check: `t` in the transcript view opened the theme picker behind it
    snap = waiting_snap(agent_row(7, "waiting", "finished"))
    source = StubSource(snap)
    source.sessions["s7"] = SessionState(
        "s7", pane_id=7, status="waiting", kind="finished", transcript_path=Path("/nonexistent")
    )
    app = DeckApp(source, interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.deck_tree.move_cursor(app.deck_tree.root.children[0].children[0])
        await pilot.press("i", "t")
        await pilot.pause()
        assert isinstance(app.screen, TranscriptScreen)
        await pilot.press("t", "w", "a", "i")
        await pilot.pause()
        assert isinstance(app.screen, TranscriptScreen)
        assert not app.waiting_only


async def test_cap_notice_shows_once() -> None:
    capped = replace(SNAP, summary_capped=True, summary_limit=5.0)
    source = StubSource(capped, replace(capped, n_panes=capped.n_panes + 1))
    app = DeckApp(source, interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.action_refresh()
        await settle(app, pilot)
        messages = [str(n.message) for n in app._notifications]  # noqa: SLF001
        notices = [m for m in messages if "Summary limit" in m]
        assert notices == [
            r"Summary limit of $5.00 reached for this month; showing raw reasons. "
            r"Change it in \[summary] in projects.toml."
        ]


async def test_detail_gets_summary_info() -> None:
    snap = waiting_snap(agent_row(7, "waiting", "finished"))
    source = StubSource(snap)
    source.sessions["s7"] = SessionState("s7", pane_id=7, status="waiting", kind="finished")
    source.info = SummaryInfo("Did x; nothing asked", 0.1, 5.0)
    app = DeckApp(source, interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.deck_tree.move_cursor(app.deck_tree.root.children[0].children[0])
        await pilot.press("i")
        await pilot.pause()
        assert isinstance(app.screen, DetailScreen) and app.screen.info == source.info


GIB = 2**30


def resource_snap(docker_rss: int = 2 * GIB) -> DeckSnapshot:
    claude = replace(row(1), label="claude", mcp_rss=GIB // 4)
    webshop = replace(
        group("config", "webshop", Path("/repos/webshop"), claude),
        docker=(DockerLine("docker: webshop-db-1", docker_rss, 30.0),),
    )
    other = replace(group("other", "Other", None, row(4)), outside_rss=9 * GIB, outside_cpu_pct=1.0)
    return DeckSnapshot(groups=(webshop, other), n_panes=2)


async def test_tree_shows_mcp_docker_and_outside_lines() -> None:
    nav = StubNavigator()
    app = DeckApp(StubSource(resource_snap()), navigator=nav, interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        lines = [str(n.label) for n in all_nodes(app)]
        assert any("└ MCP servers" in line for line in lines)
        assert any("docker: webshop-db-1" in line for line in lines)
        assert any("outside WezTerm" in line for line in lines)
        ids = [node_id(n.data) for n in all_nodes(app)]
        assert ids == [
            "g:config:/repos/webshop", "p:1", "m:1", "d:config:/repos/webshop:docker: webshop-db-1",
            "g:other:None", "p:4", "o:other:None",
        ]  # fmt: skip
        for target in (2, 3, 6):  # mcp jumps to its pane; docker and outside do nothing
            app.deck_tree.move_cursor(all_nodes(app)[target])
            await pilot.press("enter")
            await settle(app, pilot)
        assert nav.jumps == [1]


async def test_cursor_on_docker_line_survives_refresh() -> None:
    app = DeckApp(StubSource(resource_snap(), resource_snap(3 * GIB)), interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.deck_tree.move_cursor(all_nodes(app)[3])
        await pilot.pause()
        app.action_refresh()
        await settle(app, pilot)
        cursor = app.deck_tree.cursor_node
        assert cursor is not None and "RAM 3.0G" in str(cursor.label)


async def test_machine_header() -> None:
    m = MachineSample(32 * GIB, 10 * GIB, 0, 0, 38.0, 16)
    with_machine = replace(SNAP, machine_view=MachineView(m, None, "▄", "▂"))
    app = DeckApp(StubSource(SNAP, with_machine), interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        header = app.query_one("#machine", Static)
        assert not header.display
        app.action_refresh()
        await settle(app, pilot)
        assert header.display and "RAM 22.0 / 32.0G (69%)" in str(header.render())


async def test_rows_show_their_share_of_the_machine() -> None:
    m = MachineSample(32 * GIB, 10 * GIB, 0, 0, 38.0, 16)
    snap = replace(resource_snap(8 * GIB), machine=m)
    app = DeckApp(StubSource(resource_snap(8 * GIB), snap), interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        assert not any("%)" in str(n.label) for n in all_nodes(app))  # no machine sample yet
        app.action_refresh()
        await settle(app, pilot)
        labels = [str(n.label) for n in all_nodes(app)]
        # project, pane, MCP, docker, Other, pane, outside WezTerm
        assert [label.count("%)") for label in labels] == [1] * 7
        assert "RAM 8.0G (25%)" in labels[3] and "RAM 9.0G (28%)" in labels[6]


async def test_waiting_pane_with_mcp_has_unique_ids() -> None:
    waiting = replace(agent_row(7, "waiting", "question"), mcp_rss=GIB // 4)
    app = DeckApp(StubSource(waiting_snap(waiting)), interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        ids = [node_id(n.data) for n in all_nodes(app)]
        assert len(ids) == len(set(ids)), ids
        assert ids == ["g:waiting:None", "w:7", "g:config:/repos/webshop", "p:7", "m:7", "r:7"]


async def test_resources_hint_once() -> None:
    calls: list[int] = []

    def hint() -> str:
        calls.append(1)
        return "Run `cardak resources install`"

    app = DeckApp(StubSource(SNAP), interval=3600, resources_status=hint)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.action_refresh()
        await settle(app, pilot)
        assert calls == [1]
        assert any("resources install" in str(n.message) for n in app._notifications)  # noqa: SLF001


CRASH = "WezTerm was closed at 15:28:48 on Oct 21. The machine ran out of memory (32 GB)."


def crash_log() -> FakeLog:

    now = time.time()
    return FakeLog(
        events=[
            ResourceEvent(now - 100, "big", "warning", "pane:3", "pane 3 · zsh · 9.0 GB", True),
            ResourceEvent(now - 50, "crash", "critical", "wezterm:7", CRASH, True),
        ]
    )


async def test_crash_banner_and_dismiss() -> None:
    log = crash_log()
    app = DeckApp(StubSource(SNAP), interval=3600, log=log)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        banner = app.query_one("#crash", Static)
        assert banner.display
        assert str(banner.render()).startswith(
            "WezTerm was closed at 15:28:48 on Oct 21. The machine ran out of memory (32 GB). · d:"
        )
        await pilot.press("d")
        assert not banner.display and log.dismissed == [log.events[1].ts]


async def test_event_log_screen_lists_newest_first() -> None:
    from cardak.ui.events import EventLogScreen

    app = DeckApp(StubSource(SNAP), interval=3600, log=crash_log())
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("e")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, EventLogScreen)
        first, second = screen.query_one(Tree).root.children
        assert "WezTerm closed" in str(first.label) and "big" in str(second.label)
        assert len(first.children) == 2  # the full report, one sentence per line
        await pilot.press("escape")
        assert not isinstance(app.screen, EventLogScreen)


async def test_event_log_without_log_notifies() -> None:
    app = DeckApp(StubSource(SNAP), interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("e")
        await pilot.pause()
        assert [n.message for n in app._notifications] == ["Event log unavailable."]
        assert not app.query_one("#crash", Static).display


async def test_event_log_survives_markup_in_texts() -> None:
    from cardak.ui.events import EventLogScreen

    now = time.time()
    odd = "pane 3 · claude · fix [/bold] [link] · 9.0 GB"
    log = FakeLog(
        events=[
            ResourceEvent(now - 60, "big", "warning", "pane:3", odd, True),
            ResourceEvent(
                now - 50, "crash", "critical", "wezterm:7", f"WezTerm was closed. {odd}.", True
            ),
        ]
    )
    app = DeckApp(StubSource(SNAP), interval=3600, log=log)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("e")
        await pilot.pause()
        assert isinstance(app.screen, EventLogScreen)
        tree = app.screen.query_one(Tree)
        tree.root.children[0].expand()
        await pilot.pause()
        assert "[/bold]" in str(tree.root.children[0].children[1].label)


class Sends:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[tuple[int, str]] = []
        self.error = error

    def __call__(self, pane_id: int, text: str) -> None:
        self.calls.append((pane_id, text))
        if self.error is not None:
            raise self.error


def notices(app: DeckApp) -> list[str]:
    return [str(n.message) for n in app._notifications]  # noqa: SLF001


async def checkpoint_app(
    status: str = "idle",
    kind: str | None = None,
    sends: Sends | None = None,
    skills: str | None = None,
) -> tuple[DeckApp, Sends]:
    sends = sends or Sends()
    snap = waiting_snap(others=(agent_row(7, status, kind), row(2)))
    source = StubSource(snap)
    source.sessions["s7"] = SessionState("s7", pane_id=7, status=status, kind=kind)  # type: ignore[arg-type]
    app = DeckApp(source, interval=3600, send=sends, skills_status=lambda: skills)
    return app, sends


async def test_c_confirm_sends_command() -> None:
    from cardak.ui.confirm import ConfirmScreen

    app, sends = await checkpoint_app()
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.deck_tree.move_cursor(app.deck_tree.root.children[0])  # the project: one Claude pane
        await pilot.press("c")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        text = " ".join(str(s.render()) for s in app.screen.query(Static))
        assert "Send /checkpoint to pane 7 · webshop · claude?" in text
        assert "Anything already typed in that pane's input is sent with it." in text
        await pilot.press("enter")
        await settle(app, pilot)
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert sends.calls == [(7, "/checkpoint"), (7, "\r")]  # Enter as its own keystroke
        assert "Sent /checkpoint to pane 7." in notices(app)


async def test_c_escape_sends_nothing() -> None:
    app, sends = await checkpoint_app()
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.deck_tree.move_cursor(app.deck_tree.root.children[0].children[0])
        await pilot.press("c")
        await pilot.pause()
        await pilot.press("escape")
        await settle(app, pilot)
        assert sends.calls == []


async def test_c_refused_working_shows_reason() -> None:
    app, sends = await checkpoint_app("working")
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.deck_tree.move_cursor(app.deck_tree.root.children[0].children[0])
        await pilot.press("c")
        await pilot.pause()
        assert "Claude is working; checkpoint when it's done." in notices(app)
        assert sends.calls == []


async def test_c_skills_missing_hint_no_send() -> None:
    app, sends = await checkpoint_app(skills="Run `cardak skills install` to add them.")
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.deck_tree.move_cursor(app.deck_tree.root.children[0].children[0])
        await pilot.press("c")
        await pilot.pause()
        assert "Run `cardak skills install` to add them." in notices(app)
        assert sends.calls == []


async def test_c_send_error_notified() -> None:
    from cardak.collectors.wezterm import WeztermUnavailable

    app, sends = await checkpoint_app(
        sends=Sends(WeztermUnavailable("no WezTerm GUI socket found"))
    )
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.deck_tree.move_cursor(app.deck_tree.root.children[0].children[0])
        await pilot.press("c")
        await pilot.pause()
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "Couldn't send: no WezTerm GUI socket found" in notices(app)


async def test_R_refreshes_and_r_opens_recap() -> None:
    from cardak.ui.recap import RecapScreen

    source = StubSource(SNAP)
    polls: list[int] = []
    poll = source.poll
    source.poll = lambda: polls.append(1) or poll()  # type: ignore[method-assign]
    app = DeckApp(source, interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        before = len(polls)
        await pilot.press("R")
        await settle(app, pilot)
        assert len(polls) > before
        app.deck_tree.move_cursor(app.deck_tree.root.children[0])
        await pilot.press("r")
        await pilot.pause()
        assert isinstance(app.screen, RecapScreen)


async def test_r_on_other_header_says_select() -> None:
    app = DeckApp(StubSource(SNAP), interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.deck_tree.move_cursor(app.deck_tree.root.children[2])
        await pilot.press("r")
        await pilot.pause()
        assert "Select a project or a pane." in notices(app)


async def test_c_rechecks_the_session_on_enter() -> None:
    app, sends = await checkpoint_app()
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.deck_tree.move_cursor(app.deck_tree.root.children[0].children[0])
        await pilot.press("c")
        await pilot.pause()
        # while the dialog is open, Claude resumes and stops at a permission prompt
        app.source.sessions["s7"] = SessionState(  # type: ignore[attr-defined]
            "s7", pane_id=7, status="waiting", kind="permission", current_tool="Bash: rm x"
        )
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert sends.calls == []
        assert "Claude is waiting for a permission answer." in notices(app)


async def test_c_session_gone_on_enter() -> None:
    app, sends = await checkpoint_app()
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.deck_tree.move_cursor(app.deck_tree.root.children[0].children[0])
        await pilot.press("c")
        await pilot.pause()
        app.source.sessions["s7"] = SessionState("s7", pane_id=7, ended_at=5.0)  # type: ignore[attr-defined]
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert sends.calls == []
        assert "That session has ended or moved; nothing sent." in notices(app)


async def test_i_enter_jumps_for_a_session_matched_by_process() -> None:
    snap = waiting_snap(agent_row(7, "waiting", "finished"))
    source = StubSource(snap)
    # joined to its pane by the claude pid, not WEZTERM_PANE: the session knows no pane
    source.sessions["s7"] = SessionState("s7", pane_id=None, status="waiting", kind="finished")
    nav = StubNavigator()
    app = DeckApp(source, navigator=nav, interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.deck_tree.move_cursor(app.deck_tree.root.children[0].children[0])
        await pilot.press("i")
        await pilot.pause()
        assert isinstance(app.screen, DetailScreen)
        await pilot.press("enter")
        await settle(app, pilot)
        assert nav.jumps == [7]


async def test_config_error_banner_names_the_file_once() -> None:
    broken = replace(SNAP, config_error="projects.toml line 2: Invalid value")
    app = DeckApp(StubSource(broken), interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        banner = str(app.query_one("#banner", Static).render())
        assert banner == "projects.toml line 2: Invalid value"


def test_header_separator_is_a_dot() -> None:
    app = DeckApp(StubSource(SNAP), interval=3600)
    assert (
        app.format_title("cardak", "3 projects · 7 panes").plain == "cardak · 3 projects · 7 panes"
    )
    assert app.format_title("cardak", "").plain == "cardak"


async def test_poll_right_after_a_keystroke_keeps_the_best_match() -> None:
    app = DeckApp(StubSource(SNAP), interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.deck_tree.move_cursor(app.deck_tree.root.children[0])
        await pilot.pause()
        # a keystroke and a poll in the same frame (#16)
        app.find_query = "cardak"
        app._render(follow_best=True)  # noqa: SLF001
        app.apply_snapshot(replace(SNAP, config_warnings=("new poll",)))
        await pilot.pause()
        await pilot.pause()
        cursor = app.deck_tree.cursor_node
        assert cursor is not None and cursor.data is not None and cursor.data.row is not None
        assert cursor.data.row.pane_id == app._best  # noqa: SLF001


async def test_jump_from_waiting_lands_on_the_project_row() -> None:
    before = waiting_snap(agent_row(7, "waiting", "finished"), others=(row(2),))
    # seen: it left the waiting group; pane 2 now comes first, so no line number lines up
    after = waiting_snap(others=(row(2), row(3), agent_row(7, "idle")))
    nav = StubNavigator()
    app = DeckApp(StubSource(before, after), navigator=nav, interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.deck_tree.move_cursor(app.deck_tree.root.children[0].children[0])  # w:7
        await pilot.press("enter")
        await settle(app, pilot)
        await pilot.pause()
        assert nav.jumps == [7]
        cursor = app.deck_tree.cursor_node
        assert cursor is not None and cursor.data is not None
        assert node_id(cursor.data) == "p:7"  # not row 0 (#39)


async def test_cap_notice_rearms() -> None:
    capped = replace(SNAP, summary_capped=True, summary_limit=5.0)
    source = StubSource(capped, SNAP, capped)
    app = DeckApp(source, interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        for _ in range(2):
            app.action_refresh()
            await settle(app, pilot)
        notices = [str(n.message) for n in app._notifications if "Summary limit" in str(n.message)]  # noqa: SLF001
        assert len(notices) == 2  # a new month that hits the cap again says so again (#55)


class PreviewRecorder:
    in_wezterm = True

    def __init__(self) -> None:
        self.calls: list[tuple[Path, str]] = []

    def preview(self, project_path: Path, scheme: str) -> bool:
        self.calls.append((project_path, scheme))
        return True


class FailingThemeSource(StubSource):
    def set_theme(self, project_path: Path, theme: str, name: str | None = None) -> None:
        raise ConfigError(Path("projects.toml"), "is read-only")


async def choose_nord(app: DeckApp, pilot: Pilot[None], row: int) -> None:
    await settle(app, pilot)
    app.deck_tree.move_cursor(app.deck_tree.root.children[row])
    await pilot.press("t")
    await settle(app, pilot)
    await pilot.press("enter")
    await settle(app, pilot)


async def test_failed_save_clears_the_preview() -> None:
    rec = PreviewRecorder()
    app = DeckApp(
        FailingThemeSource(SNAP), interval=3600, schemes=SchemeStore(THEMES), previewer=rec
    )
    async with app.run_test() as pilot:
        await choose_nord(app, pilot, 1)
        assert rec.calls[-1] == (Path("/repos/cardak"), "")  # no reload is coming to clear it


async def test_saving_the_current_scheme_clears_the_preview() -> None:
    themed = replace(
        SNAP, groups=(SNAP.groups[0], replace(SNAP.groups[1], theme="Nord"), SNAP.groups[2])
    )
    rec = PreviewRecorder()
    source = StubSource(themed)
    app = DeckApp(source, interval=3600, schemes=SchemeStore(THEMES), previewer=rec)
    async with app.run_test() as pilot:
        await choose_nord(app, pilot, 1)
        assert rec.calls[-1] == (Path("/repos/cardak"), "")  # unchanged config: no reload


async def test_picker_load_regenerates() -> None:
    source = StubSource(SNAP)
    store = SchemeStore()
    app = DeckApp(source, interval=3600, schemes=store, load_schemes=lambda: THEMES)
    app.call_from_thread = lambda *a, **k: None  # type: ignore[method-assign]  # no running app
    app._schemes_for_picker()  # noqa: SLF001  # the picker's worker, before startup's load
    assert store.get() == THEMES and source.regenerated == 1  # tints get derived colors (#25)


def test_one_probe_for_startup_and_picker() -> None:
    import threading

    calls: list[int] = []

    def slow_probe() -> dict:
        calls.append(1)
        time.sleep(0.1)
        return THEMES

    app = DeckApp(StubSource(SNAP), interval=3600, schemes=SchemeStore(), load_schemes=slow_probe)
    app.call_from_thread = lambda *a, **k: None  # type: ignore[method-assign]  # no running app
    startup = threading.Thread(target=app._load_schemes)  # noqa: SLF001
    startup.start()
    time.sleep(0.02)  # the picker opens while the startup probe runs
    assert app._schemes_for_picker() == THEMES  # noqa: SLF001
    startup.join()
    assert len(calls) == 1


class Tabs:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[tuple[Path, int | None]] = []
        self.error = error

    def __call__(self, cwd: Path, near: int | None) -> int:
        self.calls.append((cwd, near))
        if self.error is not None:
            raise self.error
        return 50


def cleanup_app(tabs: Tabs | None = None) -> tuple[DeckApp, Tabs, Sends, StubNavigator]:
    tabs, sends, jumps = tabs or Tabs(), Sends(), StubNavigator()
    app = DeckApp(StubSource(SNAP), navigator=jumps, interval=3600, send=sends, open_tab=tabs)
    return app, tabs, sends, jumps


async def test_x_opens_a_tab_and_starts_repo_cleanup() -> None:
    from cardak.ui.confirm import ConfirmScreen

    app, tabs, sends, jumps = cleanup_app()
    async with app.run_test() as pilot:
        await settle(app, pilot)
        project = app.deck_tree.root.children[0]
        app.deck_tree.move_cursor(project)
        await pilot.press("x")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        text = " ".join(str(s.render()) for s in app.screen.query(Static))
        group = SNAP.groups[0]
        assert (
            f"Open a new tab in {group.path or group.key} and run `claude /repo-cleanup`?" in text
        )
        assert "Enter: open" in text
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await settle(app, pilot)
        await app.workers.wait_for_complete()
        assert tabs.calls == [(group.key, group.panes[0].pane_id)]
        assert sends.calls == [(50, "claude /repo-cleanup"), (50, "\r")]
        assert jumps.jumps == [50]
        assert "Started /repo-cleanup in pane 50." in notices(app)


async def test_x_escape_opens_nothing() -> None:
    app, tabs, sends, _ = cleanup_app()
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.deck_tree.move_cursor(app.deck_tree.root.children[0])
        await pilot.press("x")
        await pilot.pause()
        await pilot.press("escape")
        await settle(app, pilot)
        assert tabs.calls == [] and sends.calls == []


async def test_x_on_other_says_select() -> None:
    app, tabs, _, _ = cleanup_app()
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.deck_tree.move_cursor(app.deck_tree.root.children[2])
        await pilot.press("x")
        await pilot.pause()
        assert "Select a project or one of its panes." in notices(app)
        assert tabs.calls == []


async def test_x_spawn_error_notified() -> None:
    from cardak.collectors.wezterm import WeztermUnavailable

    app, _, sends, jumps = cleanup_app(Tabs(WeztermUnavailable("wezterm cli timed out")))
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.deck_tree.move_cursor(app.deck_tree.root.children[0])
        await pilot.press("x")
        await pilot.pause()
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "Couldn't open a tab: wezterm cli timed out" in notices(app)
        assert sends.calls == [] and jumps.jumps == []


async def test_x_on_a_pane_opens_next_to_it() -> None:
    app, tabs, _, _ = cleanup_app()
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.deck_tree.move_cursor(app.deck_tree.root.children[1].children[1])  # cardak, pane 3
        await pilot.press("x")
        await pilot.pause()
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert tabs.calls == [(Path("/repos/cardak"), 3)]


async def test_x_send_error_still_takes_you_to_the_tab() -> None:
    from cardak.collectors.wezterm import WeztermUnavailable

    sends = Sends(WeztermUnavailable("wezterm cli timed out"))
    jumps = StubNavigator()
    app = DeckApp(StubSource(SNAP), navigator=jumps, interval=3600, send=sends, open_tab=Tabs())
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.deck_tree.move_cursor(app.deck_tree.root.children[0])
        await pilot.press("x")
        await pilot.pause()
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await settle(app, pilot)
        await app.workers.wait_for_complete()
        assert "Opened pane 50 but couldn't type into it: wezterm cli timed out" in notices(app)
        assert jumps.jumps == [50]


async def test_deck_shows_limits_row() -> None:
    from cardak.core.limits import LimitsView, LimitView

    view = LimitsView(LimitView(27, 2e9, "ok"), LimitView(61, 2.1e9, "ok"), 0.0)
    app = DeckApp(StubSource(SNAP, replace(SNAP, plan_limits=view)), interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        row = app.query_one("#limits", Static)
        assert not row.display
        app.action_refresh()
        await settle(app, pilot)
        assert row.display and "27% left" in str(row.render()) and "61% left" in str(row.render())
        assert "\n" not in str(row.render())
