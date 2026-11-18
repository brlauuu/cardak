from collections.abc import Mapping
from pathlib import Path

from textual.app import App
from textual.widgets import Checkbox, Input, OptionList, Static

from cardak.core.backgrounds import Image
from cardak.core.config import Position
from cardak.core.themes import Scheme
from cardak.ui.theme_picker import ThemeChoice, ThemePickerScreen, swatch_label

ANSI = ("#000000",) * 8
SCHEMES = {
    n: Scheme(n, "#101010", "#e0e0e0", None, ANSI) for n in ("Nord", "Tokyo Night", "Builtin Dark")
}
PATH = Path("/r/web")


class Recorder:
    in_wezterm = True

    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def preview(
        self,
        project_path: Path,
        scheme: str,
        background: Image | bool | None = None,
        position: Position | None = None,
    ) -> bool:
        call = (project_path, scheme) if background is None else (project_path, scheme, background)
        self.calls.append(call if position is None else (*call, position))
        return True


class Host(App[None]):
    def __init__(self, screen: ThemePickerScreen) -> None:
        super().__init__()
        self.picker = screen
        self.result: ThemeChoice | str | None = "unset"

    def on_mount(self) -> None:
        def done(value: ThemeChoice | None) -> None:
            self.result = value

        self.push_screen(self.picker, done)


def picker(
    load=lambda: SCHEMES, current: str | None = "Nord", **kw: object
) -> tuple[Host, Recorder]:
    rec = Recorder()
    return Host(ThemePickerScreen("web", PATH, current, load, rec, **kw)), rec  # type: ignore[arg-type]


async def ready(host: Host, pilot) -> None:  # noqa: ANN001
    await host.workers.wait_for_complete()
    await pilot.pause()
    await pilot.pause()


def options(host: Host) -> list[str]:
    ol = host.picker.query_one(OptionList)
    return [str(ol.get_option_at_index(i).id) for i in range(ol.option_count)]


def test_swatch_label_marks_current() -> None:
    assert swatch_label(SCHEMES["Nord"], current=True).plain == "██ ██ Nord •"
    assert swatch_label(SCHEMES["Nord"], current=False).plain == "██ ██ Nord"


async def test_lists_sorted_and_highlights_current() -> None:
    host, _ = picker()
    async with host.run_test() as pilot:
        await ready(host, pilot)
        assert options(host) == ["Builtin Dark", "Nord", "Tokyo Night"]
        assert host.picker.query_one(OptionList).highlighted == 1


async def test_filter_narrows_the_list() -> None:
    host, _ = picker()
    async with host.run_test() as pilot:
        await ready(host, pilot)
        await pilot.press("t", "o", "k")
        await pilot.pause()
        assert options(host) == ["Tokyo Night"]


async def test_preview_is_debounced() -> None:
    host, rec = picker()
    async with host.run_test() as pilot:
        await ready(host, pilot)
        await pilot.press("down", "up", "down")
        await pilot.pause(ThemePickerScreen.DEBOUNCE * 3)
        assert rec.calls == [(PATH, "Tokyo Night")]


async def test_enter_saves_without_clearing_first() -> None:
    host, rec = picker()
    async with host.run_test() as pilot:
        await ready(host, pilot)
        await pilot.press("down")
        await pilot.pause(ThemePickerScreen.DEBOUNCE * 3)
        await pilot.press("enter")
        await pilot.pause(ThemePickerScreen.DEBOUNCE * 3)
        assert host.result == ThemeChoice("Tokyo Night", True)
        # no "" first: the save's config reload replaces the preview, so nothing flashes (#22)
        assert rec.calls == [(PATH, "Tokyo Night")]


async def test_escape_clears_preview_and_saves_nothing() -> None:
    host, rec = picker()
    async with host.run_test() as pilot:
        await ready(host, pilot)
        await pilot.press("down")
        await pilot.pause(ThemePickerScreen.DEBOUNCE * 3)
        await pilot.press("escape")
        await pilot.pause()
        assert host.result is None
        assert rec.calls == [(PATH, "Tokyo Night"), (PATH, "")]


async def test_escape_without_preview_sends_nothing() -> None:
    host, rec = picker()
    async with host.run_test() as pilot:
        await ready(host, pilot)
        await pilot.press("escape")
        await pilot.pause()
        assert rec.calls == []


async def test_load_failure_accepts_an_exact_name() -> None:
    def broken() -> Mapping[str, Scheme]:
        raise RuntimeError("wezterm is not installed")

    host, _ = picker(load=broken)
    async with host.run_test() as pilot:
        await ready(host, pilot)
        status = host.picker.query_one("#picker-status", Static)
        assert "wezterm is not installed" in str(status.render())
        host.picker.query_one(Input).value = "My Scheme"
        await pilot.press("enter")
        await pilot.pause()
        assert host.result == ThemeChoice("My Scheme", True)


async def test_note_when_preview_is_unavailable() -> None:
    host = Host(ThemePickerScreen("web", PATH, "Nord", lambda: SCHEMES, None))
    async with host.run_test() as pilot:
        await ready(host, pilot)
        note = host.picker.query_one("#preview-note", Static)
        assert note.display is True
        assert "preview" in str(note.render()).lower()


async def test_no_note_when_preview_works() -> None:
    host, _ = picker()
    async with host.run_test() as pilot:
        await ready(host, pilot)
        assert host.picker.query_one("#preview-note", Static).display is False


async def test_fast_enter_after_a_preview_shows_the_saved_scheme() -> None:
    # A previewed, then B chosen within the debounce: A must not stay on the window, since
    # after the reload cardak.lua leaves alone a scheme its table doesn't know (#22)
    host, rec = picker()
    async with host.run_test() as pilot:
        await ready(host, pilot)
        await pilot.press("down")  # Tokyo Night
        await pilot.pause(ThemePickerScreen.DEBOUNCE * 3)
        await pilot.press("up")  # back to Nord, then Enter before the debounce
        await pilot.press("enter")
        await pilot.pause(ThemePickerScreen.DEBOUNCE * 3)
        assert host.result == ThemeChoice("Nord", True)
        assert rec.calls == [(PATH, "Tokyo Night"), (PATH, "Nord")]


async def test_unknown_typed_name_after_a_preview_clears_it() -> None:
    host, rec = picker()
    async with host.run_test() as pilot:
        await ready(host, pilot)
        await pilot.press("down")
        await pilot.pause(ThemePickerScreen.DEBOUNCE * 3)
        await pilot.press(*"zzz")  # no match: Enter takes the typed name
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert host.result == ThemeChoice("zzz", True)
        assert rec.calls[-1] == (PATH, "")  # it will never reach cardak.lua's table


ART = Image(Path("/c/bg/x.png"), 30, 5)


async def test_picker_returns_the_switch() -> None:
    host, _ = picker()
    async with host.run_test() as pilot:
        await ready(host, pilot)
        await pilot.press("ctrl+b")
        await pilot.pause()
        assert host.picker.query_one(Checkbox).value is False
        await pilot.press("enter")
        await pilot.pause()
        assert host.result == ThemeChoice("Nord", False)


async def test_picker_starts_from_the_saved_switch() -> None:
    host, _ = picker(background=False)
    async with host.run_test() as pilot:
        await ready(host, pilot)
        await pilot.press("enter")
        await pilot.pause()
        assert host.result == ThemeChoice("Nord", False)


async def test_picker_previews_the_switch() -> None:
    host, rec = picker(preview_image=lambda scheme: ART)
    async with host.run_test() as pilot:
        await ready(host, pilot)
        await pilot.press("ctrl+b")
        await pilot.pause()
        assert rec.calls == [(PATH, "Nord", False)]
        await pilot.press("ctrl+b")
        await pilot.pause()
        assert rec.calls[-1] == (PATH, "Nord", ART)


async def test_picker_previews_the_art_in_the_highlighted_scheme() -> None:
    asked: list[str] = []

    def image(scheme: str) -> Image:
        asked.append(scheme)
        return ART

    host, rec = picker(preview_image=image)
    async with host.run_test() as pilot:
        await ready(host, pilot)
        await pilot.press("down")
        await pilot.pause(ThemePickerScreen.DEBOUNCE * 3)
        assert rec.calls == [(PATH, "Tokyo Night", ART)] and asked == ["Tokyo Night"]


async def test_picker_shows_the_art_path() -> None:
    host, _ = picker(art_file=Path.home() / ".config/cardak/logos/web.txt")
    async with host.run_test() as pilot:
        await ready(host, pilot)
        text = str(host.picker.query_one("#art-path", Static).render())
        assert "~/.config/cardak/logos/web.txt" in text


async def test_picker_says_when_art_is_made() -> None:
    host, _ = picker()
    async with host.run_test() as pilot:
        await ready(host, pilot)
        assert "made when you save" in str(host.picker.query_one("#art-path", Static).render())


async def test_escape_discards_the_switch() -> None:
    host, rec = picker()
    async with host.run_test() as pilot:
        await ready(host, pilot)
        await pilot.press("ctrl+b")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert host.result is None
        assert rec.calls[-1] == (PATH, "")


OFF_REASON = "background off for every project: [background] enabled = false"


async def test_with_backgrounds_off_the_picker_says_so_and_previews_none() -> None:
    asked: list[str] = []

    def image(scheme: str) -> Image:
        asked.append(scheme)
        return ART

    host, rec = picker(
        preview_image=image, art_file=Path("/art/web.txt"), backgrounds_off=OFF_REASON
    )
    async with host.run_test() as pilot:
        await ready(host, pilot)
        box = host.picker.query_one(Checkbox)
        assert box.disabled
        assert str(host.picker.query_one("#art-path", Static).render()) == OFF_REASON
        await pilot.press("ctrl+b")
        await pilot.pause()
        assert box.value is True and rec.calls == []  # ctrl+b does nothing
        await pilot.press("down")
        await pilot.pause(ThemePickerScreen.DEBOUNCE * 3)
        assert rec.calls == [(PATH, "Tokyo Night")] and asked == []  # the scheme, no art
        await pilot.press("enter")
        await pilot.pause()
        assert host.result == ThemeChoice("Tokyo Night", True)  # the project's switch is kept


async def test_art_not_made_yet_is_made_when_the_background_is_turned_on() -> None:
    host, _ = picker(background=False)
    async with host.run_test() as pilot:
        await ready(host, pilot)

        def line() -> str:
            return str(host.picker.query_one("#art-path", Static).render())

        assert "made when the background is turned on" in line()
        await pilot.press("ctrl+b")
        await pilot.pause()
        assert "made when you save" in line()


def position_line(host: Host) -> str:
    return str(host.picker.query_one("#art-position", Static).render())


async def test_the_picker_says_where_the_art_sits_and_how_to_move_it() -> None:
    # #175
    host, _ = picker(position=("right", "bottom"))
    async with host.run_test() as pilot:
        await ready(host, pilot)
        assert position_line(host) == "position: bottom right (ctrl+l to move)"
        await pilot.press("ctrl+b")
        await pilot.pause()
        assert position_line(host) == "position: bottom right (with the background on)"


async def test_a_custom_position_is_named_as_such() -> None:
    host, _ = picker(position=(3, "top"))
    async with host.run_test() as pilot:
        await ready(host, pilot)
        assert (
            position_line(host) == "position: top, 3 cells from the right, custom (ctrl+l to move)"
        )


async def test_ctrl_l_steps_through_the_spots_and_previews_each() -> None:
    host, rec = picker(position=("center", "bottom"), preview_image=lambda scheme: ART)
    async with host.run_test() as pilot:
        await ready(host, pilot)
        await pilot.press("ctrl+l")
        await pilot.pause()
        assert position_line(host) == "position: bottom right (ctrl+l to move)"
        assert rec.calls == [(PATH, "Nord", ART, ("right", "bottom"))]
        await pilot.press("ctrl+l")  # round again
        await pilot.pause()
        assert position_line(host).startswith("position: top left ")
        await pilot.press("down")  # another scheme keeps the moved art
        await pilot.pause(ThemePickerScreen.DEBOUNCE * 3)
        assert rec.calls[-1] == (PATH, "Tokyo Night", ART, ("left", "top"))
        await pilot.press("enter")
        await pilot.pause()
        assert host.result == ThemeChoice("Tokyo Night", True, ("left", "top"))


async def test_ctrl_l_from_a_custom_position_starts_at_top_left() -> None:
    host, _ = picker(position=(3, 1))
    async with host.run_test() as pilot:
        await ready(host, pilot)
        await pilot.press("ctrl+l")
        await pilot.pause()
        assert position_line(host).startswith("position: top left ")


async def test_without_ctrl_l_the_position_is_left_alone() -> None:
    host, rec = picker(position=(3, 1))
    async with host.run_test() as pilot:
        await ready(host, pilot)
        await pilot.press("ctrl+b", "ctrl+b")
        await pilot.pause()
        assert all(len(call) < 4 for call in rec.calls)  # no position previewed
        await pilot.press("enter")
        await pilot.pause()
        assert host.result == ThemeChoice("Nord", True)  # position None: unchanged


async def test_ctrl_l_does_nothing_with_the_background_off_or_backgrounds_off() -> None:
    host, rec = picker(position=("right", "bottom"), background=False)
    async with host.run_test() as pilot:
        await ready(host, pilot)
        await pilot.press("ctrl+l")
        await pilot.pause()
        assert rec.calls == [] and "bottom right" in position_line(host)
    host, rec = picker(position=("right", "bottom"), backgrounds_off=OFF_REASON)
    async with host.run_test() as pilot:
        await ready(host, pilot)
        assert not host.picker.query_one("#art-position", Static).display
        await pilot.press("ctrl+l")
        await pilot.pause()
        assert rec.calls == []


async def test_escape_after_ctrl_l_clears_the_preview_and_saves_nothing() -> None:
    host, rec = picker(position=("right", "bottom"))
    async with host.run_test() as pilot:
        await ready(host, pilot)
        await pilot.press("ctrl+l")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert host.result is None and rec.calls[-1] == (PATH, "")


async def test_ctrl_l_comes_back_round_to_a_custom_position() -> None:
    # review of #175: a stray ctrl+l mustn't lose a numbered position
    host, _ = picker(position=(3, 1))
    async with host.run_test() as pilot:
        await ready(host, pilot)
        await pilot.press(*["ctrl+l"] * 10)
        await pilot.pause()
        line = "position: 3 cells from the right, 1 cell down, custom"
        assert position_line(host).startswith(line)
        await pilot.press("ctrl+l")
        await pilot.pause()
        assert position_line(host).startswith("position: top left ")


async def test_a_move_is_dropped_when_the_background_is_turned_off() -> None:
    host, _ = picker(position=("right", "bottom"))
    async with host.run_test() as pilot:
        await ready(host, pilot)
        await pilot.press("ctrl+l", "ctrl+b", "enter")
        await pilot.pause()
        assert host.result == ThemeChoice("Nord", False)
