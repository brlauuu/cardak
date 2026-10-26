from collections.abc import Mapping
from pathlib import Path

from textual.app import App
from textual.widgets import Input, OptionList, Static

from cardak.core.themes import Scheme
from cardak.ui.theme_picker import ThemePickerScreen, swatch_label

ANSI = ("#000000",) * 8
SCHEMES = {
    n: Scheme(n, "#101010", "#e0e0e0", None, ANSI) for n in ("Nord", "Tokyo Night", "Builtin Dark")
}
PATH = Path("/r/web")


class Recorder:
    in_wezterm = True

    def __init__(self) -> None:
        self.calls: list[tuple[Path, str]] = []

    def preview(self, project_path: Path, scheme: str) -> bool:
        self.calls.append((project_path, scheme))
        return True


class Host(App[None]):
    def __init__(self, screen: ThemePickerScreen) -> None:
        super().__init__()
        self.picker = screen
        self.result: str | None = "unset"

    def on_mount(self) -> None:
        def done(value: str | None) -> None:
            self.result = value

        self.push_screen(self.picker, done)


def picker(load=lambda: SCHEMES, current: str | None = "Nord") -> tuple[Host, Recorder]:
    rec = Recorder()
    return Host(ThemePickerScreen("web", PATH, current, load, rec)), rec


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
        assert host.result == "Tokyo Night"
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
        assert host.result == "My Scheme"


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
