"""A yes/no question over the deck: Enter confirms, Esc cancels."""

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static


class ConfirmScreen(ModalScreen[bool]):
    BINDINGS = [
        Binding("enter", "answer(True)", "Send"),
        Binding("escape", "answer(False)", "Cancel"),
    ]
    CSS = """
    ConfirmScreen { align: center middle; }
    #confirm { width: 70; height: auto; border: round $accent; padding: 1 2; background: $surface; }
    #confirm-note, #confirm-keys { color: $text-muted; margin-top: 1; }
    """

    def __init__(self, question: str, note: str) -> None:
        super().__init__()
        self._question = question
        self._note = note

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm"):  # Text: names and labels are free text, not markup
            yield Static(Text(self._question), id="confirm-question")
            yield Static(Text(self._note), id="confirm-note")
            yield Static(Text("Enter: send · Esc: cancel"), id="confirm-keys")

    def action_answer(self, yes: bool) -> None:
        self.dismiss(yes)
