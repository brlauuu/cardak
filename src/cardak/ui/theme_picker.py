"""Pick a color scheme for a project, whether its ASCII art shows behind its windows (#9) and
where it sits (#175), previewing all three live on the project's windows."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.fuzzy import Matcher
from textual.markup import escape
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import Checkbox, Input, Label, OptionList, Static
from textual.widgets.option_list import Option

from cardak.core.backgrounds import Image
from cardak.core.config import DEFAULT_POSITION, SPOTS, Position, spot_name
from cardak.core.themes import Scheme

SchemeLoader = Callable[[], Mapping[str, Scheme]]


class ThemePreviewer(Protocol):
    in_wezterm: bool

    def preview(
        self,
        project_path: Path,
        scheme: str,
        background: Image | bool | None = None,
        position: Position | None = None,
    ) -> bool: ...


class Backgrounds(Protocol):
    """What the picker needs to show a project's art (#9)."""

    def off_reason(self) -> str | None: ...

    def art_file(self, project_path: Path) -> Path | None: ...

    def preview_image(self, project_path: Path, scheme: str) -> Image | None: ...


@dataclass(frozen=True)
class ThemeChoice:
    scheme: str
    background: bool
    position: Position | None = None  # where ctrl+l moved the art; None: left as it was (#175)


def _tilde(path: Path) -> str:
    home = Path.home()
    return f"~/{path.relative_to(home)}" if path.is_relative_to(home) else str(path)


def swatch_label(scheme: Scheme, current: bool) -> Text:
    text = Text()
    text.append("██", style=scheme.background)
    text.append(" ")
    text.append("██", style=scheme.foreground)
    text.append(f" {scheme.name}")
    if current:
        text.append(" •", style="bold")
    return text


class ThemePickerScreen(ModalScreen[ThemeChoice | None]):
    DEBOUNCE = 0.15
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("down", "move(1)", show=False),
        Binding("up", "move(-1)", show=False),
        Binding("ctrl+b", "toggle_background", "Background", priority=True),
        Binding("ctrl+l", "move_art", "Move art", priority=True),
    ]
    DEFAULT_CSS = """
    ThemePickerScreen { align: center middle; }
    #picker { width: 70; height: 28; border: round $accent; background: $surface; padding: 0 1; }
    #background { border: none; padding: 0; height: 1; }
    #art-path, #art-position { color: $text-muted; }
    #schemes { height: 1fr; }
    """

    def __init__(
        self,
        project_name: str,
        project_path: Path,
        current: str | None,
        load: SchemeLoader,
        previewer: ThemePreviewer | None,
        background: bool = True,
        art_file: Path | None = None,
        preview_image: Callable[[str], Image | None] | None = None,
        backgrounds_off: str | None = None,
        position: Position = DEFAULT_POSITION,
    ) -> None:
        super().__init__()
        self._name = project_name
        self._path = project_path
        self._current = current
        self._load = load
        self._previewer = previewer
        self._schemes: Mapping[str, Scheme] = {}
        self._timer: Timer | None = None
        self._previewed = False
        self._last_previewed: str | None = None
        self._background = background
        self._art_file = art_file
        self._preview_image = preview_image
        # why no project shows art ([background] enabled = false, #126; no extra, #131)
        self._backgrounds_off = backgrounds_off
        self._position = position  # where the art sits now: the project's, else the default
        # ctrl+l's stops: the nine spots, and a custom position last, so it can be got back
        self._stops = SPOTS if position in SPOTS else (*SPOTS, position)
        self._moved = False  # ctrl+l pressed: previews and the choice carry the position

    def compose(self) -> ComposeResult:
        with Vertical(id="picker"):
            yield Label(f"Theme for {escape(self._name)}")
            yield Input(placeholder="filter schemes", id="scheme-find")
            yield Static("loading schemes…", id="picker-status")
            yield Static(
                "No live preview: the deck isn't running inside WezTerm.", id="preview-note"
            )
            yield OptionList(id="schemes")
            yield Checkbox(
                "Background: the project's ASCII art (ctrl+b)",
                self._background,
                id="background",
                disabled=self._backgrounds_off is not None,
            )
            yield Static(escape(self._position_line(self._background)), id="art-position")
            yield Static(escape(self._art_line(self._background)), id="art-path")

    def _position_line(self, on: bool) -> str:
        custom = "" if self._position in SPOTS else ", custom"
        how = "ctrl+l to move" if on else "with the background on"
        return f"position: {spot_name(self._position)}{custom} ({how})"

    def _art_line(self, on: bool) -> str:
        if self._backgrounds_off is not None:
            return self._backgrounds_off
        if self._art_file:
            return f"art: {_tilde(self._art_file)}; replace its text to change it"
        return "art: made when you save" if on else "art: made when the background is turned on"

    def on_mount(self) -> None:
        self.query_one(Input).focus()
        self.query_one("#art-position", Static).display = self._backgrounds_off is None
        self.query_one("#preview-note", Static).display = not (
            self._previewer is not None and self._previewer.in_wezterm
        )
        self.run_worker(self._load_worker, thread=True, exit_on_error=False)

    def _load_worker(self) -> None:
        try:
            schemes = self._load()
        except Exception as e:  # the loader belongs to cli; any failure is just a message here
            self.app.call_from_thread(self._failed, str(e))
            return
        self.app.call_from_thread(self._loaded, schemes)

    def _loaded(self, schemes: Mapping[str, Scheme]) -> None:
        self._schemes = schemes
        self.query_one("#picker-status", Static).display = False
        self._refill()

    def _failed(self, reason: str) -> None:
        self.query_one("#picker-status", Static).update(
            escape(f"Couldn't list schemes: {reason}. Type an exact name and press Enter.")
        )

    def _refill(self) -> None:
        query = self.query_one(Input).value.strip()
        names = sorted(self._schemes)
        if query:
            matcher = Matcher(query)
            names = [n for n in names if matcher.match(n) > 0]
        options = self.query_one(OptionList)
        options.clear_options()
        options.add_options(
            Option(swatch_label(self._schemes[n], n == self._current), id=n) for n in names
        )
        if names:
            options.highlighted = names.index(self._current) if self._current in names else 0

    @on(Input.Changed, "#scheme-find")
    def _filter(self) -> None:
        self._refill()

    def action_move(self, step: int) -> None:
        options = self.query_one(OptionList)
        if step > 0:
            options.action_cursor_down()
        else:
            options.action_cursor_up()

    @on(OptionList.OptionHighlighted)
    def _highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if self._timer is not None:
            self._timer.stop()
        name = event.option.id
        # The current scheme is already on screen; preview it only to undo another preview.
        if name is None or (name == self._current and not self._previewed):
            return
        self._timer = self.set_timer(self.DEBOUNCE, lambda: self._preview(name))

    def _background_arg(self, name: str) -> Image | bool | None:
        """Off → False; on → the art in that scheme's color, None (the saved one) without it.
        With backgrounds off for every project, None: the scheme alone, as before #9."""
        if self._backgrounds_off is not None:
            return None
        if not self.query_one("#background", Checkbox).value:
            return False
        return self._preview_image(name) if self._preview_image else None

    def action_toggle_background(self) -> None:
        box = self.query_one("#background", Checkbox)
        if box.disabled:
            return
        box.value = not box.value

    def action_move_art(self) -> None:
        """The next of the nine spots (#175), then the custom position it started at, if any."""
        if self._backgrounds_off is not None or not self.query_one("#background", Checkbox).value:
            return
        at = self._stops.index(self._position) + 1
        self._position, self._moved = self._stops[at % len(self._stops)], True
        self.query_one("#art-position", Static).update(escape(self._position_line(True)))
        self._preview_now()

    @on(Checkbox.Changed, "#background")
    def _background_changed(self, event: Checkbox.Changed) -> None:
        self.query_one("#art-path", Static).update(escape(self._art_line(event.value)))
        self.query_one("#art-position", Static).update(escape(self._position_line(event.value)))
        self._preview_now()

    def _preview_now(self) -> None:
        """Preview the highlighted scheme at once, else the last one previewed or the saved."""
        options = self.query_one(OptionList)
        highlighted = options.highlighted
        name = None
        if highlighted is not None and options.option_count:
            name = options.get_option_at_index(highlighted).id
        name = name or self._last_previewed or self._current
        if name:
            if self._timer is not None:
                self._timer.stop()
            self._preview(name)

    def _preview(self, name: str) -> None:
        background = self._background_arg(name)
        position = self._position if self._moved else None
        if self._previewer is not None and self._previewer.preview(
            self._path, name, background, position
        ):
            self._previewed = True
            self._last_previewed = name

    def _clear_preview(self) -> None:
        if self._timer is not None:
            self._timer.stop()
        if self._previewer is not None and self._previewed:
            self._previewer.preview(self._path, "")

    @on(OptionList.OptionSelected)
    def _selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id is not None:
            self._choose(event.option.id)

    @on(Input.Submitted, "#scheme-find")
    def _submitted(self, event: Input.Submitted) -> None:
        options = self.query_one(OptionList)
        if options.highlighted is not None and options.option_count:
            name = options.get_option_at_index(options.highlighted).id
            if name is not None:
                self._choose(name)
        elif event.value.strip():
            self._choose(event.value.strip())

    def _choose(self, name: str) -> None:
        # Never "" before a save: clearing shows the old colors until the reload (#22). Show
        # the scheme being saved instead: after the reload cardak.lua keeps only schemes its
        # table knows, so a leftover preview of another one would stay on the window. A name
        # the list doesn't know never reaches that table, so that preview is cleared. The deck
        # clears it too when the save writes nothing.
        if self._timer is not None:
            self._timer.stop()
        if self._previewed and self._last_previewed != name and self._previewer is not None:
            known = name in self._schemes
            if known:
                self._preview(name)
            else:
                self._previewer.preview(self._path, "")
        on = self.query_one("#background", Checkbox).value
        # with the art off, a move is dropped: the line said it needs the background on
        self.dismiss(ThemeChoice(name, on, self._position if self._moved and on else None))

    def action_cancel(self) -> None:
        self._clear_preview()
        self.dismiss(None)
