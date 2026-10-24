"""The event log: resource alerts, crash reports and daemon starts, newest first."""

from collections.abc import Sequence

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Header, Tree

from cardak.core.eventlog import line_text, log_lines, sentences
from cardak.core.store import ResourceEvent

EMPTY = "No events yet. The daemon writes them: cardak resources install."
STYLES = {"critical": "bold red", "warning": "yellow", "info": "dim"}


class EventLogScreen(Screen[None]):
    BINDINGS = [Binding("escape", "app.pop_screen", "Back")]

    def __init__(self, events: Sequence[ResourceEvent]) -> None:
        super().__init__()
        self._events = events

    def compose(self) -> ComposeResult:
        yield Header()
        yield Tree[None]("events", id="events")
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = "event log · last 30 days"
        tree = self.query_one(Tree)
        tree.show_root = False
        lines = log_lines(self._events)
        if not lines:
            tree.root.add_leaf(Text(EMPTY))
        for line in lines:
            label = Text(line_text(line), style=STYLES.get(line.level, ""))
            if line.full is None:
                tree.root.add_leaf(label)
                continue
            node = tree.root.add(label, expand=False)
            for sentence in sentences(line.full):
                node.add_leaf(Text(sentence))  # Text: pane labels are not markup
        tree.focus()
