import threading
from collections.abc import Mapping
from pathlib import Path

from textual.app import App
from textual.widgets import Input, OptionList, Static

from cardak.core.backgrounds import Image
from cardak.core.config import Position
from cardak.core.themes import Scheme
from cardak.ui.art_pane import ArtPane
from cardak.ui.theme_picker import NONE_ID, ThemeChoice, ThemePickerScreen, swatch_label

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
        scheme: str | None,
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


def labels(host: Host) -> list[str]:
    ol = host.picker.query_one(OptionList)
    return [str(ol.get_option_at_index(i).prompt) for i in range(ol.option_count)]


def said(host: Host, widget: str) -> str:
    return str(host.picker.query_one(widget, Static).render())


def art(host: Host) -> ArtPane:
    return host.picker.query_one(ArtPane)


def notices(host: Host) -> list[str]:
    return [n.message for n in host._notifications]


ART = Image(Path("/c/bg/x.png"), 30, 5)
OFF_REASON = "background off for every project: [background] enabled = false"
CONFIG = Path("/home/u/.config/cardak/projects.toml")


def test_swatch_label_marks_current() -> None:
    assert swatch_label(SCHEMES["Nord"], current=True).plain == "██ ██ Nord •"
    assert swatch_label(SCHEMES["Nord"], current=False).plain == "██ ██ Nord"


# the Schemes pane


async def test_lists_none_then_sorted_and_highlights_current() -> None:
    host, _ = picker()
    async with host.run_test() as pilot:
        await ready(host, pilot)
        assert options(host) == [NONE_ID, "Builtin Dark", "Nord", "Tokyo Night"]
        assert host.picker.query_one(OptionList).highlighted == 2
        assert said(host, "#picker-title") == "Theme and art: web"


async def test_filter_narrows_the_list_and_hides_none() -> None:
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


def broken() -> Mapping[str, Scheme]:
    raise RuntimeError("wezterm is not installed")


async def test_load_failure_accepts_an_exact_name() -> None:
    host, _ = picker(load=broken)
    async with host.run_test() as pilot:
        await ready(host, pilot)
        assert "wezterm is not installed" in said(host, "#picker-status")
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


# "none": no scheme of the project's own (#182)


async def test_none_is_highlighted_for_a_project_without_a_theme_and_saves_as_none() -> None:
    host, rec = picker(current=None)
    async with host.run_test() as pilot:
        await ready(host, pilot)
        assert host.picker.query_one(OptionList).highlighted == 0
        assert labels(host)[0] == "none (WezTerm's own colors) •"
        assert said(host, "#unsaved") == ""
        await pilot.press("enter")
        await pilot.pause()
        assert host.result == ThemeChoice(None, True) and rec.calls == []


async def test_none_says_what_the_project_gets_instead() -> None:
    for inherits, label in (
        ((None, "Dracula"), "none (the default: Dracula)"),
        (("outer", "Nord"), "none (as outer: Nord)"),
    ):
        host, _ = picker(inherits=inherits)
        async with host.run_test() as pilot:
            await ready(host, pilot)
            assert labels(host)[0] == label


async def test_none_on_a_themed_project_previews_it_and_saving_clears_the_preview() -> None:
    host, rec = picker(preview_image=lambda scheme: ART)
    async with host.run_test() as pilot:
        await ready(host, pilot)
        await pilot.press("up", "up")
        await pilot.pause(ThemePickerScreen.DEBOUNCE * 3)
        assert rec.calls == [(PATH, None, ART)]
        assert said(host, "#unsaved") == "not saved: theme"
        await pilot.press("enter")
        await pilot.pause()
        assert host.result == ThemeChoice(None, True)
        assert rec.calls[-1] == (PATH, "")  # no scheme of its own: nothing replaces a preview


async def test_the_art_moves_on_a_project_without_a_theme() -> None:
    host, rec = picker(current=None, preview_image=lambda scheme: ART)
    async with host.run_test() as pilot:
        await ready(host, pilot)
        await pilot.press("tab", "left")
        await pilot.pause()
        assert rec.calls == [(PATH, None, ART, ("center", "bottom"))]
        await pilot.press("enter")
        await pilot.pause()
        assert host.result == ThemeChoice(None, True, ("center", "bottom"))


async def test_enter_keeps_the_theme_while_the_schemes_load_or_after_they_failed() -> None:
    # review focus 1: "none" is the first row, and must not be what an art-only save picks
    gate = threading.Event()

    def slow() -> Mapping[str, Scheme]:
        gate.wait(5)
        return SCHEMES

    for load in (slow, broken):
        host, _ = picker(load=load)
        async with host.run_test() as pilot:
            await pilot.pause()
            assert labels(host) == ["none (WezTerm's own colors)", "Nord (current)"]
            await pilot.press("tab", "left", "enter")
            await pilot.pause()
            assert host.result == ThemeChoice("Nord", True, ("center", "bottom"))
            gate.set()
            await host.workers.wait_for_complete()


async def test_a_scheme_wezterm_does_not_know_is_still_a_row() -> None:
    host, _ = picker(current="Gone")
    async with host.run_test() as pilot:
        await ready(host, pilot)
        assert labels(host)[1] == "Gone (current)"
        assert host.picker.query_one(OptionList).highlighted == 1
        await pilot.press("enter")
        await pilot.pause()
        assert host.result == ThemeChoice("Gone", True)


# saving from anywhere, and what the dialog says about it


async def test_enter_saves_from_the_art_pane_and_after_a_click_on_the_switch() -> None:
    host, _ = picker()
    async with host.run_test(size=(140, 40)) as pilot:
        await ready(host, pilot)
        await pilot.press("tab")
        assert art(host).has_focus
        await pilot.press("up", "enter")
        await pilot.pause()
        assert host.result == ThemeChoice("Nord", True, ("right", "center"))
    host, _ = picker()
    async with host.run_test(size=(140, 40)) as pilot:
        await ready(host, pilot)
        await pilot.click("#art-switch")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert host.result == ThemeChoice("Nord", False)


async def test_a_filtered_row_is_saved_from_the_art_pane_never_none() -> None:
    # review focus 2
    host, _ = picker()
    async with host.run_test() as pilot:
        await ready(host, pilot)
        await pilot.press(*"tok", "tab", "enter")
        await pilot.pause()
        assert host.result == ThemeChoice("Tokyo Night", True)
    host, _ = picker()
    async with host.run_test() as pilot:
        await ready(host, pilot)
        await pilot.press(*"zzz", "tab", "enter")
        await pilot.pause()
        assert host.result == ThemeChoice("zzz", True)


async def test_the_key_line_follows_the_pane() -> None:
    host, _ = picker()
    async with host.run_test() as pilot:
        await ready(host, pilot)
        assert said(host, "#keys") == "tab Art · enter Save · esc Cancel"
        await pilot.press("tab")
        await pilot.pause()
        assert said(host, "#keys") == "tab Schemes · enter Save · esc Cancel"
        await pilot.press("shift+tab")
        await pilot.pause()
        assert host.picker.query_one(Input).has_focus
        assert said(host, "#keys") == "tab Art · enter Save · esc Cancel"


async def test_what_is_not_saved_is_said_and_unsaid() -> None:
    host, _ = picker()
    async with host.run_test() as pilot:
        await ready(host, pilot)
        assert said(host, "#unsaved") == ""
        await pilot.press("down")
        await pilot.pause()
        assert said(host, "#unsaved") == "not saved: theme"
        await pilot.press("tab", "left")
        await pilot.pause()
        assert said(host, "#unsaved") == "not saved: theme, position"
        await pilot.press("space")
        await pilot.pause()
        assert said(host, "#unsaved") == "not saved: theme, art off"
        await pilot.press("space", "r", "shift+tab", "up")
        await pilot.pause()
        assert said(host, "#unsaved") == ""


# the Art pane in the dialog


async def test_picker_returns_the_switch() -> None:
    host, _ = picker()
    async with host.run_test() as pilot:
        await ready(host, pilot)
        await pilot.press("tab", "space")
        await pilot.pause()
        assert art(host).on is False
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
        await pilot.press("tab", "space")
        await pilot.pause()
        assert rec.calls == [(PATH, "Nord", False)]
        await pilot.press("space")
        await pilot.pause()
        assert rec.calls[-1] == (PATH, "Nord", ART)


async def test_picker_previews_the_art_in_the_highlighted_scheme() -> None:
    asked: list[str | None] = []

    def image(scheme: str | None) -> Image:
        asked.append(scheme)
        return ART

    host, rec = picker(preview_image=image)
    async with host.run_test() as pilot:
        await ready(host, pilot)
        await pilot.press("down")
        await pilot.pause(ThemePickerScreen.DEBOUNCE * 3)
        assert rec.calls == [(PATH, "Tokyo Night", ART)] and asked == ["Tokyo Night"]


async def test_picker_shows_the_art_path_and_the_config() -> None:
    host, _ = picker(
        art_file=Path.home() / ".config/cardak/logos/web.txt",
        config_file=Path.home() / ".config/cardak/projects.toml",
    )
    async with host.run_test() as pilot:
        await ready(host, pilot)
        assert "~/.config/cardak/logos/web.txt" in said(host, "#art-file")
        assert "~/.config/cardak/projects.toml" in said(host, "#config-file")


async def test_picker_says_when_art_is_made() -> None:
    host, _ = picker()
    async with host.run_test() as pilot:
        await ready(host, pilot)
        assert "made when you save" in said(host, "#art-file")
    host, _ = picker(configured=False)
    async with host.run_test() as pilot:
        await ready(host, pilot)
        assert "made once this is saved as a project" in said(host, "#art-file")


async def test_escape_discards_the_switch() -> None:
    host, rec = picker()
    async with host.run_test() as pilot:
        await ready(host, pilot)
        await pilot.press("tab", "space")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert host.result is None
        assert rec.calls[-1] == (PATH, "")


async def test_with_backgrounds_off_the_picker_says_so_and_previews_none() -> None:
    asked: list[str | None] = []

    def image(scheme: str | None) -> Image:
        asked.append(scheme)
        return ART

    opened: list[Path] = []
    host, rec = picker(
        preview_image=image,
        art_file=Path("/art/web.txt"),
        backgrounds_off=OFF_REASON,
        config_file=CONFIG,
        open_file=lambda path: opened.append(path),
    )
    async with host.run_test() as pilot:
        await ready(host, pilot)
        assert said(host, "#art-off") == OFF_REASON
        await pilot.press("tab", "space", "left")
        await pilot.pause()
        assert rec.calls == []  # neither does anything
        await pilot.press("c")
        await host.workers.wait_for_complete()
        assert opened == [CONFIG]  # where it can be turned on
        await pilot.press("shift+tab", "down")
        await pilot.pause(ThemePickerScreen.DEBOUNCE * 3)
        assert rec.calls == [(PATH, "Tokyo Night")] and asked == []  # the scheme, no art
        await pilot.press("enter")
        await pilot.pause()
        assert host.result == ThemeChoice("Tokyo Night", True)  # the project's switch is kept


async def test_arrows_move_the_art_and_preview_each_spot() -> None:
    host, rec = picker(position=("center", "bottom"), preview_image=lambda scheme: ART)
    async with host.run_test() as pilot:
        await ready(host, pilot)
        await pilot.press("tab", "left")
        await pilot.pause()
        assert "bottom left" in said(host, "#art-grid")
        assert rec.calls == [(PATH, "Nord", ART, ("left", "bottom"))]
        await pilot.press("up", "up", "shift+tab", "down")  # another scheme keeps the moved art
        await pilot.pause(ThemePickerScreen.DEBOUNCE * 3)
        assert rec.calls[-1] == (PATH, "Tokyo Night", ART, ("left", "top"))
        await pilot.press("enter")
        await pilot.pause()
        assert host.result == ThemeChoice("Tokyo Night", True, ("left", "top"))


async def test_without_a_move_the_position_is_left_alone() -> None:
    host, rec = picker(position=(3, 1))
    async with host.run_test() as pilot:
        await ready(host, pilot)
        await pilot.press("tab", "space", "space")
        await pilot.pause()
        assert all(len(call) < 4 for call in rec.calls)  # no position previewed
        await pilot.press("enter")
        await pilot.pause()
        assert host.result == ThemeChoice("Nord", True)  # position None: unchanged


async def test_escape_after_a_move_clears_the_preview_and_saves_nothing() -> None:
    host, rec = picker(position=("right", "bottom"))
    async with host.run_test() as pilot:
        await ready(host, pilot)
        await pilot.press("tab", "left")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert host.result is None and rec.calls[-1] == (PATH, "")


async def test_a_stray_arrow_does_not_lose_a_custom_position() -> None:
    host, _ = picker(position=(3, 1))
    async with host.run_test() as pilot:
        await ready(host, pilot)
        await pilot.press("tab", "left", "r", "enter")
        await pilot.pause()
        assert host.result == ThemeChoice("Nord", True)


async def test_a_move_is_dropped_when_the_art_is_turned_off() -> None:
    host, _ = picker(position=("right", "bottom"))
    async with host.run_test() as pilot:
        await ready(host, pilot)
        await pilot.press("tab", "left", "space", "enter")
        await pilot.pause()
        assert host.result == ThemeChoice("Nord", False)


# opening the files


async def test_c_and_e_open_the_files_and_say_so() -> None:
    opened: list[Path] = []
    host, _ = picker(
        art_file=Path("/art/web.txt"), config_file=CONFIG, open_file=lambda p: opened.append(p)
    )
    async with host.run_test() as pilot:
        await ready(host, pilot)
        await pilot.press("tab", "c", "e")
        await host.workers.wait_for_complete()
        await pilot.pause()
        assert opened == [CONFIG, Path("/art/web.txt")]
        assert f"Opened {CONFIG}" in notices(host)
        assert host.result == "unset"  # the dialog stays open


async def test_a_file_that_does_not_open_is_named_in_full_with_the_reason() -> None:
    host, _ = picker(config_file=CONFIG, open_file=lambda p: "xdg-open isn't installed")
    async with host.run_test() as pilot:
        await ready(host, pilot)
        await pilot.press("tab", "c")
        await host.workers.wait_for_complete()
        await pilot.pause()
        assert notices(host) == [f"Couldn't open {CONFIG}: xdg-open isn't installed"]

    def boom(path: Path) -> str | None:
        raise OSError("no display")

    for opener, why in ((boom, "no display"), (None, "can't open files in this deck")):
        host, _ = picker(config_file=CONFIG, open_file=opener)
        async with host.run_test() as pilot:
            await ready(host, pilot)
            await pilot.press("tab", "c")
            await host.workers.wait_for_complete()
            await pilot.pause()
            assert notices(host) == [f"Couldn't open {CONFIG}: {why}"]


# layout


async def test_a_name_with_brackets_is_shown_as_it_is() -> None:
    # review focus 3
    rec = Recorder()
    host = Host(ThemePickerScreen("we[b]", PATH, "Nord", lambda: SCHEMES, rec))
    async with host.run_test() as pilot:
        await ready(host, pilot)
        assert said(host, "#picker-title") == "Theme and art: we[b]"


async def test_the_art_pane_goes_below_the_list_on_a_narrow_screen() -> None:
    # review focus 4
    host, _ = picker()
    async with host.run_test(size=(60, 30)) as pilot:
        await ready(host, pilot)
        box = host.picker.query_one("#picker")
        assert box.has_class("-stacked")
        assert art(host).region.y > host.picker.query_one("#schemes").region.y
        await pilot.resize_terminal(140, 40)
        await pilot.pause()
        assert not box.has_class("-stacked")
        assert art(host).region.x > host.picker.query_one("#schemes").region.x
        assert art(host).region.y == host.picker.query_one("#schemes-pane").region.y
        await pilot.resize_terminal(60, 30)
        await pilot.pause()
        assert box.has_class("-stacked")


async def test_none_is_found_by_typing_it_and_the_list_pages() -> None:
    # live check of #182: with ~900 schemes "none" was hundreds of rows above the current one
    host, _ = picker()
    async with host.run_test() as pilot:
        await ready(host, pilot)
        await pilot.press(*"none")
        await pilot.pause()
        assert options(host)[0] == NONE_ID
        assert host.picker.query_one(OptionList).highlighted == 0
        await pilot.press("enter")
        await pilot.pause()
        assert host.result == ThemeChoice(None, True)
    many = {f"S{i:03}": Scheme(f"S{i:03}", "#101010", "#e0e0e0", None, ANSI) for i in range(200)}
    host, _ = picker(load=lambda: many, current="S150")
    async with host.run_test(size=(140, 40)) as pilot:
        await ready(host, pilot)
        ol = host.picker.query_one(OptionList)
        assert ol.highlighted == 151
        await pilot.press("pageup")
        await pilot.pause()
        assert ol.highlighted is not None and ol.highlighted < 145
        await pilot.press("pagedown", "pagedown")
        await pilot.pause()
        assert ol.highlighted > 151


async def test_a_small_terminal_still_shows_a_usable_list() -> None:
    host, _ = picker()
    async with host.run_test(size=(80, 24)) as pilot:
        await ready(host, pilot)
        assert host.picker.query_one("#schemes").size.height >= 4  # none and the three schemes
        assert art(host).region.bottom <= 24
