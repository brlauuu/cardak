"""The Art pane of the theme and art dialog (#182): a project's art switch, where the art
sits in a grid of nine spots, and the two plain text files behind it."""

from pathlib import Path

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Static

from cardak.core.config import COLUMNS, ROWS, SPOTS, Position, spot_name, step_spot


def tilde(path: Path) -> str:
    try:
        return f"~/{path.relative_to(Path.home())}"
    except ValueError:
        return str(path)


class _Line(Static):
    """One line of the pane; a click on it does what its key does."""

    def __init__(self, id: str, action: str | None = None) -> None:
        super().__init__(id=id)
        self._action = action

    async def on_click(self, event: events.Click) -> None:
        if self._action is not None and isinstance(self.parent, ArtPane):
            event.stop()
            await self.parent.run_action(self._action)


class ArtPane(Vertical, can_focus=True):
    BINDINGS = [
        Binding("space", "switch", show=False),
        Binding("left", "step(-1, 0)", show=False),
        Binding("right", "step(1, 0)", show=False),
        Binding("up", "step(0, -1)", show=False),
        Binding("down", "step(0, 1)", show=False),
        Binding("r", "reset", show=False),
        Binding("e", "open_art", show=False),
        Binding("c", "open_config", show=False),
    ]
    DEFAULT_CSS = """
    ArtPane { height: auto; }
    ArtPane > Static { height: auto; }
    #art-grid, #art-files { margin-top: 1; }
    #art-files, #art-off { color: $text-muted; }
    """

    class Changed(Message):
        """The switch or the position changed."""

    class Open(Message):
        """A file of the pane is to be opened."""

        def __init__(self, path: Path) -> None:
            super().__init__()
            self.path = path

    def __init__(
        self,
        *,
        on: bool,
        position: Position,
        art_file: Path | None,
        config_file: Path | None,
        off_reason: str | None,
        configured: bool = True,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self.on = on
        self.position = position
        self._was = (on, position)
        self._art_file = art_file
        self._config_file = config_file
        self._off_reason = off_reason  # why no project shows art; then only the config is here
        self._configured = configured

    @property
    def moved(self) -> bool:
        return self.position != self._was[1]

    def changes(self) -> list[str]:
        """What differs from the values the pane opened with. A move goes with the art: with
        the art off nothing is placed."""
        if self._off_reason is not None:
            return []
        said = [] if self.on == self._was[0] else ["art on" if self.on else "art off"]
        return said + (["position"] if self.on and self.moved else [])

    def compose(self) -> ComposeResult:
        if self._off_reason is not None:
            yield Static(Text(self._off_reason), id="art-off")
        else:
            yield _Line("art-switch", "switch")
            yield Static(id="art-grid")
        yield Static(Text("Plain text files, yours to edit:"), id="art-files")
        if self._off_reason is None:
            yield _Line("art-file", "open_art")
        yield _Line("config-file", "open_config")

    def on_mount(self) -> None:
        self._show()

    def _show(self) -> None:
        if self._off_reason is None:
            box = "[x]" if self.on else "[ ]"
            self.query_one("#art-switch", Static).update(_keyed(f"{box} show art", "space"))
            self.query_one("#art-grid", Static).update(self._grid())
            self.query_one("#art-file", Static).update(self._art_line())
        config = self._config_file
        line = _file("c", "config", tilde(config)) if config else Text("c  config  (none)")
        self.query_one("#config-file", Static).update(line)

    def _grid(self) -> Text:
        name = spot_name(self.position) + ("" if self.position in SPOTS else ", custom")
        beside = (name, "arrows move, r resets" if self.on else "with the art on", "")
        text = Text()
        for row, said in zip(ROWS, beside, strict=True):
            dots = " ".join("●" if (column, row) == self.position else "·" for column in COLUMNS)
            text.append(f"  {dots}", style="" if self.on else "dim")
            text.append(f"   {said}\n" if said else "\n", style="" if said == name else "dim")
        text.rstrip()
        return text

    def _art_line(self) -> Text:
        if self._art_file is not None:
            return _file("e", "art", tilde(self._art_file))
        if not self._configured:
            return Text("e  art     made once this is saved as a project", style="dim")
        when = "made when you save" if self.on else "made when the art is turned on"
        return Text(f"e  art     {when}", style="dim")

    def _changed(self) -> None:
        self._show()
        self.post_message(self.Changed())

    def action_switch(self) -> None:
        if self._off_reason is None:
            self.on = not self.on
            self._changed()

    def action_step(self, columns: int, rows: int) -> None:
        if self._off_reason is not None or not self.on:
            return
        at = step_spot(self.position, columns, rows)
        if at != self.position:
            self.position = at
            self._changed()

    def action_reset(self) -> None:
        if self.moved:
            self.position = self._was[1]
            self._changed()

    def action_open_art(self) -> None:
        if self._off_reason is None and self._art_file is not None:
            self.post_message(self.Open(self._art_file))

    def action_open_config(self) -> None:
        if self._config_file is not None:
            self.post_message(self.Open(self._config_file))


def _keyed(said: str, key: str) -> Text:
    text = Text(said)
    text.append(f"   {key}", style="dim")
    return text


def _file(key: str, what: str, path: str) -> Text:
    """`e  art     ~/…/web.txt`: the path underlined, as a link is."""
    text = Text(f"{key}  {what:<8}")
    text.append(path, style="underline")
    return text
