import threading
from dataclasses import replace
from pathlib import Path

from textual.pilot import Pilot
from textual.widgets import Input, Static

from cardak.core.agents import SessionState
from cardak.core.config import ConfigError
from cardak.core.navigate import JumpResult
from cardak.core.resolver import Kind
from cardak.core.snapshot import WAITING_NAME, AgentView, DeckSnapshot, Group, PaneRow
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
        assert labels(app) == ["WezTerm not reachable: no WezTerm GUI socket found — retrying"]


async def test_poll_exception_shows_internal_error() -> None:
    app = DeckApp(BrokenSource(SNAP), interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        assert "internal error: RuntimeError('boom')" in labels(app)[0]
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
