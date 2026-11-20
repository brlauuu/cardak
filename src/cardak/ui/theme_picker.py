"""The theme and art dialog (#182): a project's color scheme on the left, its ASCII art on the
right (whether it shows, #9, where it sits, #175, and the text files behind it), previewed
live on the project's windows and saved together with Enter."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.fuzzy import Matcher
from textual.markup import escape
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import Input, Label, OptionList, Static
from textual.widgets.option_list import Option

from cardak.core.backgrounds import Image
from cardak.core.config import DEFAULT_POSITION, Position
from cardak.core.themes import Scheme
from cardak.ui.art_pane import ArtPane, tilde

SchemeLoader = Callable[[], Mapping[str, Scheme]]

NONE_ID = "\0none"  # the row for no scheme of the project's own; no scheme has this name
SIDE_BY_SIDE_AT = 100  # screen columns from which the two panes fit beside each other


class ThemePreviewer(Protocol):
    in_wezterm: bool

    def preview(
        self,
        project_path: Path,
        scheme: str | None,
        background: Image | bool | None = None,
        position: Position | None = None,
    ) -> bool: ...


class Backgrounds(Protocol):
    """What the picker needs to show a project's art (#9)."""

    def off_reason(self) -> str | None: ...

    def art_file(self, project_path: Path) -> Path | None: ...

    def preview_image(self, project_path: Path, scheme: str | None) -> Image | None: ...


@dataclass(frozen=True)
class ThemeChoice:
    scheme: str | None  # None: no scheme of its own (the "none" row, #182)
    background: bool
    position: Position | None = None  # where the art was moved to; None: left as it was (#175)


def swatch_label(scheme: Scheme, current: bool) -> Text:
    text = Text()
    text.append("██", style=scheme.background)
    text.append(" ")
    text.append("██", style=scheme.foreground)
    text.append(f" {scheme.name}")
    if current:
        text.append(" •", style="bold")
    return text


def none_label(inherits: tuple[str | None, str] | None, current: bool) -> Text:
    """The "none" row, naming what the project's windows get without a scheme of its own."""
    if inherits is None:
        gets = "WezTerm's own colors"
    elif inherits[0] is None:
        gets = f"the default: {inherits[1]}"
    else:
        gets = f"as {inherits[0]}: {inherits[1]}"
    text = Text(f"none ({gets})")
    if current:
        text.append(" •", style="bold")
    return text


class ThemePickerScreen(ModalScreen[ThemeChoice | None]):
    DEBOUNCE = 0.15
    BINDINGS = [
        Binding("enter", "save", "Save", priority=True),
        Binding("escape", "cancel", "Cancel"),
        Binding("tab", "switch_pane", "Switch pane", priority=True),
        Binding("shift+tab", "switch_pane", show=False, priority=True),
        Binding("down", "move(1)", show=False),
        Binding("up", "move(-1)", show=False),
        Binding("pagedown", "page(1)", show=False),
        Binding("pageup", "page(-1)", show=False),
    ]
    DEFAULT_CSS = """
    ThemePickerScreen { align: center middle; }
    #picker {
        width: 118; max-width: 100%; height: 28; max-height: 100%;
        border: round $accent; background: $surface; padding: 0 1;
    }
    #picker.-stacked { width: 72; height: 34; }
    #picker-head { height: 1; }
    #picker-title { width: 1fr; }
    #unsaved { width: auto; color: $warning; }
    #panes { height: 1fr; }
    #picker.-stacked #panes { layout: vertical; }
    #schemes-pane { width: 1fr; height: 1fr; border: round $foreground-muted; padding: 0 1; }
    #schemes-pane:focus-within { border: round $accent; }
    #schemes { height: 1fr; }
    #art { width: 56; height: 100%; border: round $foreground-muted; padding: 0 1; }
    #art:focus { border: round $accent; }
    #picker.-stacked #art { width: 100%; height: auto; }
    #picker.-stacked #scheme-find { border: none; height: 1; padding: 0; }
    #picker.-stacked #art-grid, #picker.-stacked #art-files { margin-top: 0; }
    #keys { height: 1; color: $text-muted; }
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
        preview_image: Callable[[str | None], Image | None] | None = None,
        backgrounds_off: str | None = None,
        position: Position = DEFAULT_POSITION,
        inherits: tuple[str | None, str] | None = None,
        configured: bool = True,
        config_file: Path | None = None,
        open_file: Callable[[Path], str | None] | None = None,
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
        self._preview_image = preview_image
        # why no project shows art ([background] enabled = false, #126; no extra, #131)
        self._backgrounds_off = backgrounds_off
        self._inherits = inherits  # what "none" gets: (project or None, scheme), or None
        self._open_file = open_file
        self._listed_for = ""  # the filter text the list was last filled for
        self._art = ArtPane(
            on=background,
            position=position,  # where the art sits now: the project's, else the default
            art_file=art_file,
            config_file=config_file,
            off_reason=backgrounds_off,
            configured=configured,
            id="art",
        )

    def compose(self) -> ComposeResult:
        with Vertical(id="picker"):
            with Horizontal(id="picker-head"):
                yield Label(f"Theme and art: {escape(self._name)}", id="picker-title")
                yield Static("", id="unsaved")
            with Horizontal(id="panes"):
                with Vertical(id="schemes-pane"):
                    yield Input(placeholder="filter schemes", id="scheme-find")
                    yield Static("loading schemes…", id="picker-status")
                    yield Static(
                        "No live preview: the deck isn't running inside WezTerm.",
                        id="preview-note",
                    )
                    yield OptionList(id="schemes")
                yield self._art
            yield Static("", id="keys")

    def on_mount(self) -> None:
        self.query_one("#schemes-pane").border_title = "Schemes"
        self._art.border_title = "Art"
        self._stack(self.app.size.width)
        self.query_one(Input).focus()
        self.query_one("#preview-note", Static).display = not (
            self._previewer is not None and self._previewer.in_wezterm
        )
        self._refill()  # "none" and the project's scheme are there before the list loads
        self._show_keys()
        self.run_worker(self._load_worker, thread=True, exit_on_error=False)

    def on_resize(self, event: events.Resize) -> None:
        self._stack(event.size.width)  # App.size still has the old width here

    def _stack(self, width: int) -> None:
        """Under SIDE_BY_SIDE_AT columns the Art pane goes below the list."""
        self.query_one("#picker").set_class(width < SIDE_BY_SIDE_AT, "-stacked")

    def on_descendant_focus(self) -> None:
        self._show_keys()

    def _show_keys(self) -> None:
        other = "Schemes" if self._art.has_focus else "Art"
        self.query_one("#keys", Static).update(f"tab {other} · enter Save · esc Cancel")

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

    def _rows(self, query: str) -> list[Option]:
        """The rows for a filter: "none" first (no filter, or one that spells it out), then the
        schemes. The project's scheme is always a row, even one the list doesn't have (not
        loaded, or a name WezTerm doesn't know), so
        Enter with nothing touched never changes the theme."""
        names = sorted(self._schemes)
        current = self._current
        if current is not None and current not in self._schemes:
            names.insert(0, current)
        rows = []
        if query:
            matcher = Matcher(query)
            names = [n for n in names if matcher.match(n) > 0]
        for name in names:
            scheme = self._schemes.get(name)
            label = swatch_label(scheme, name == current) if scheme else Text(f"{name} (current)")
            rows.append(Option(label, id=name))
        none = Option(none_label(self._inherits, current is None), id=NONE_ID)
        if not query or query.lower() == "none":
            rows.insert(0, none)  # spelled out, it is what was asked for: _refill highlights it
        elif len(query) > 1 and "none".startswith(query.lower()):
            # being spelled, not fuzzy ("one" is One Dark's), and last: a real list is
            # hundreds of rows long, but a filter's first row is what Enter saves
            rows.append(none)
        return rows

    def _refill(self) -> None:
        self._listed_for = self.query_one(Input).value.strip()
        rows = self._rows(self._listed_for)
        options = self.query_one(OptionList)
        options.clear_options()
        options.add_options(rows)
        if rows:
            ids = [row.id for row in rows]
            asked = self._listed_for.lower() == "none"
            mine = NONE_ID if self._current is None or asked else self._current
            options.highlighted = ids.index(mine) if mine in ids else 0
        self._show_unsaved()

    @on(Input.Changed, "#scheme-find")
    def _filter(self) -> None:
        self._refill()

    def _highlighted_id(self) -> str | None:
        """The highlighted row's id (NONE_ID for "none"); None when the list is empty."""
        options = self.query_one(OptionList)
        if options.highlighted is None or not options.option_count:
            return None
        return options.get_option_at_index(options.highlighted).id

    def _show_unsaved(self) -> None:
        at = self._highlighted_id()
        typed = self.query_one(Input).value.strip()
        theme = (at != (NONE_ID if self._current is None else self._current)) if at else bool(typed)
        parts = (["theme"] if theme else []) + self._art.changes()
        said = f"not saved: {', '.join(parts)}" if parts else ""
        self.query_one("#unsaved", Static).update(said)

    def action_move(self, step: int) -> None:
        options = self.query_one(OptionList)
        if step > 0:
            options.action_cursor_down()
        else:
            options.action_cursor_up()

    def action_page(self, step: int) -> None:
        options = self.query_one(OptionList)
        if step > 0:
            options.action_page_down()
        else:
            options.action_page_up()

    def action_switch_pane(self) -> None:
        (self.query_one(Input) if self._art.has_focus else self._art).focus()

    @on(OptionList.OptionHighlighted)
    def _highlighted(self, event: OptionList.OptionHighlighted) -> None:
        self._show_unsaved()
        if self._timer is not None:
            self._timer.stop()
        at = event.option.id
        if at is None:
            return
        scheme = None if at == NONE_ID else at
        # The current scheme is already on screen; preview it only to undo another preview.
        if scheme == self._current and not self._previewed:
            return
        self._timer = self.set_timer(self.DEBOUNCE, lambda: self._preview(scheme))

    def _background_arg(self, scheme: str | None) -> Image | bool | None:
        """Off → False; on → the art in that scheme's color, None (the saved one) without it.
        With backgrounds off for every project, None: the scheme alone, as before #9."""
        if self._backgrounds_off is not None:
            return None
        if not self._art.on:
            return False
        return self._preview_image(scheme) if self._preview_image else None

    @on(ArtPane.Changed)
    def _art_changed(self) -> None:
        self._show_unsaved()
        self._preview_now()

    def _preview_now(self) -> None:
        """Preview the highlighted scheme at once, else the last one previewed or the saved."""
        if self._timer is not None:
            self._timer.stop()
        at = self._highlighted_id()
        if at is not None:
            self._preview(None if at == NONE_ID else at)
        else:
            self._preview(self._last_previewed if self._previewed else self._current)

    def _preview(self, scheme: str | None) -> None:
        background = self._background_arg(scheme)
        position = self._art.position if self._art.moved else None
        if self._previewer is not None and self._previewer.preview(
            self._path, scheme, background, position
        ):
            self._previewed = True
            self._last_previewed = scheme

    def _clear_preview(self) -> None:
        if self._timer is not None:
            self._timer.stop()
        if self._previewer is not None and self._previewed:
            self._previewer.preview(self._path, "")

    @on(OptionList.OptionSelected)
    def _selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id is not None:  # a click: Enter is the screen's
            self._choose(None if event.option.id == NONE_ID else event.option.id)

    def action_save(self) -> None:
        """Enter, from either pane: the highlighted row, else the typed name as it is."""
        typed = self.query_one(Input).value.strip()
        if typed != self._listed_for:  # Enter came before the list followed the last key
            self._refill()
        at = self._highlighted_id()
        if at is not None:
            self._choose(None if at == NONE_ID else at)
        elif typed:
            self._choose(typed)

    def _choose(self, scheme: str | None) -> None:
        # Never "" before a save: clearing shows the old colors until the reload (#22). Show
        # the scheme being saved instead: after the reload cardak.lua keeps only schemes its
        # table knows, so a leftover preview of another one would stay on the window. A name
        # the list doesn't know never reaches that table, so that preview is cleared, and so
        # is one saved as "none": nothing of the project's is there to replace it. The deck
        # clears it too when the save writes nothing.
        if self._timer is not None:
            self._timer.stop()
        if self._previewer is not None and self._previewed:
            if scheme is None or scheme not in self._schemes:
                self._previewer.preview(self._path, "")
            elif self._last_previewed != scheme:
                self._preview(scheme)
        art = self._art
        # with the art off, a move is dropped: the pane said it needs the art on
        self.dismiss(ThemeChoice(scheme, art.on, art.position if art.moved and art.on else None))

    @on(ArtPane.Open)
    def _open(self, event: ArtPane.Open) -> None:
        path = event.path
        self.run_worker(lambda: self._open_worker(path), thread=True, exit_on_error=False)

    def _open_worker(self, path: Path) -> None:
        """xdg-open may take a moment to say it failed: off the UI thread."""
        if self._open_file is None:
            reason: str | None = "can't open files in this deck"
        else:
            try:
                reason = self._open_file(path)
            except Exception as e:  # the opener is cli's; a failure is a notice here
                reason = str(e)
        if reason is None:
            self.app.call_from_thread(self.notify, escape(f"Opened {tilde(path)}"))
        else:
            said = escape(f"Couldn't open {path}: {reason}")
            self.app.call_from_thread(self.notify, said, severity="error")

    def action_cancel(self) -> None:
        self._clear_preview()
        self.dismiss(None)
