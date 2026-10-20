import threading
from dataclasses import replace
from pathlib import Path

from textual.pilot import Pilot
from textual.widgets import Input, Static

from cardak.config import ConfigError
from cardak.resolver import Kind
from cardak.snapshot import DeckSnapshot, Group, PaneRow
from cardak.ui.app import AddProjectScreen, DeckApp


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

    def poll(self) -> DeckSnapshot:
        return self.snaps.pop(0) if len(self.snaps) > 1 else self.snaps[0]

    def add_project(self, name: str, path: Path) -> None:
        if self.fail_add:
            raise ConfigError(Path("projects.toml"), f"{path} is already configured")
        self.added.append((name, path))


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
        assert cursor.data[1] is not None and cursor.data[1].pane_id == 3


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
