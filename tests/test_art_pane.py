from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import Static

from cardak.core.config import Position
from cardak.ui.art_pane import ArtPane

ART = Path("/home/u/.config/cardak/logos/web.txt")
CONFIG = Path("/home/u/.config/cardak/projects.toml")
OFF = "background off for every project: [background] enabled = false"


class Host(App[None]):
    def __init__(self, **kw: object) -> None:
        super().__init__()
        args: dict[str, object] = {
            "on": True,
            "position": ("center", "center"),
            "art_file": ART,
            "config_file": CONFIG,
            "off_reason": None,
            "configured": True,
        }
        self.pane = ArtPane(**(args | kw), id="art")  # type: ignore[arg-type]
        self.changed = 0
        self.opened: list[Path] = []

    def compose(self) -> ComposeResult:
        yield self.pane

    def on_mount(self) -> None:
        self.pane.focus()

    def on_art_pane_changed(self) -> None:
        self.changed += 1

    def on_art_pane_open(self, event: ArtPane.Open) -> None:
        self.opened.append(event.path)


def text(host: Host, widget: str) -> str:
    return str(host.pane.query_one(widget, Static).render())


async def test_arrows_move_the_dot_and_stop_at_the_edges() -> None:
    host = Host()
    async with host.run_test() as pilot:
        await pilot.press("left", "up", "up")
        await pilot.pause()
        assert host.pane.position == ("left", "top") and host.pane.moved
        grid = text(host, "#art-grid").splitlines()
        assert grid[0].startswith("  ● · ·") and "top left" in grid[0]
        assert "arrows move, r resets" in grid[1]
        await pilot.press("right", "right", "right", "down", "down", "down")
        await pilot.pause()
        assert host.pane.position == ("right", "bottom")
        assert text(host, "#art-grid").splitlines()[2].startswith("  · · ●")
        assert host.changed == 6  # the presses that moved it; the three at an edge did not


async def test_space_toggles_and_the_arrows_need_the_art_on() -> None:
    host = Host()
    async with host.run_test() as pilot:
        assert text(host, "#art-switch").startswith("[x] show art")
        await pilot.press("space")
        await pilot.pause()
        assert host.pane.on is False and host.changed == 1
        assert text(host, "#art-switch").startswith("[ ] show art")
        assert "with the art on" in text(host, "#art-grid")
        await pilot.press("left")
        await pilot.pause()
        assert host.pane.position == ("center", "center") and host.changed == 1


async def test_a_custom_position_has_no_dot_and_r_brings_it_back() -> None:
    custom: Position = (3, 1)
    host = Host(position=custom)
    async with host.run_test() as pilot:
        grid = text(host, "#art-grid")
        assert "●" not in grid and "3 cells from the right, 1 cell down, custom" in grid
        await pilot.press("left")
        await pilot.pause()
        assert host.pane.position == ("right", "bottom") and host.pane.moved
        await pilot.press("r")
        await pilot.pause()
        assert host.pane.position == custom and not host.pane.moved


async def test_changes_says_what_differs_from_the_opening_values() -> None:
    host = Host()
    async with host.run_test() as pilot:
        assert host.pane.changes() == []
        await pilot.press("left")
        assert host.pane.changes() == ["position"]
        await pilot.press("space")
        assert host.pane.changes() == ["art off"]  # the move goes with the art
        await pilot.press("space", "r")
        assert host.pane.changes() == []
    host = Host(on=False)
    async with host.run_test() as pilot:
        await pilot.press("space")
        assert host.pane.changes() == ["art on"]


async def test_e_and_c_open_the_files_and_so_does_a_click() -> None:
    host = Host()
    async with host.run_test() as pilot:
        await pilot.press("e", "c")
        await pilot.pause()
        assert host.opened == [ART, CONFIG]
        await pilot.click("#config-file")
        await pilot.pause()
        assert host.opened[-1] == CONFIG
        assert "Plain text files, yours to edit:" in text(host, "#art-files")


async def test_no_art_file_yet_is_said_and_is_no_link() -> None:
    for kw, said in (
        ({"art_file": None}, "made when you save"),
        ({"art_file": None, "on": False}, "made when the art is turned on"),
        ({"art_file": None, "configured": False}, "made once this is saved as a project"),
    ):
        host = Host(**kw)
        async with host.run_test() as pilot:
            assert said in text(host, "#art-file")
            await pilot.press("e")
            await pilot.click("#art-file")
            await pilot.pause()
            assert host.opened == []


async def test_backgrounds_off_shows_the_reason_and_still_opens_the_config() -> None:
    host = Host(off_reason=OFF)
    async with host.run_test() as pilot:
        assert OFF in text(host, "#art-off")
        assert not host.pane.query("#art-grid") and not host.pane.query("#art-switch")
        await pilot.press("space", "left", "e", "c")
        await pilot.pause()
        assert host.opened == [CONFIG] and host.changed == 0 and host.pane.changes() == []


async def test_a_path_with_brackets_is_shown_as_it_is() -> None:
    odd = Path("/r/[x]/projects.toml")
    host = Host(config_file=odd, art_file=Path("/r/[bold]/web.txt"))
    async with host.run_test():
        assert "/r/[x]/projects.toml" in text(host, "#config-file")
        assert "/r/[bold]/web.txt" in text(host, "#art-file")
