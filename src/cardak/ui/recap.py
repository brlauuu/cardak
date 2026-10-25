"""Recap (`r`): a project's or pane's latest handoff entry, and what changed in git since."""

from collections.abc import Sequence
from pathlib import Path

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Header, Markdown, OptionList, Static

from cardak.collectors.git import GitState
from cardak.core import recap
from cardak.core.recap import Note

NO_NOTE = "No checkpoint yet. Press c on an agent pane to write one."


def _display(path: Path) -> str:
    home = Path.home()
    return f"~/{path.relative_to(home)}" if path.is_relative_to(home) else str(path)


class RecapScreen(Screen[None]):
    BINDINGS = [Binding("escape", "app.pop_screen", "Back")]
    CSS = """
    #head, #since { padding: 0 1; }
    #head { text-style: bold; }
    #note { margin: 1 0; }
    #others { height: auto; max-height: 8; }
    """

    def __init__(self, title: str, dirs: Sequence[Path], now: float) -> None:
        super().__init__()
        self._title = title
        self._dirs = list(dirs)
        self._now = now
        self._notes: list[Note] = []
        self._others: list[int] = []  # option index → index into _notes

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(Text("Loading…"), id="head")
        yield Markdown("", id="note")
        yield Static("", id="since")
        yield OptionList(id="others")
        yield Footer()

    def on_mount(self) -> None:
        self.title = f"recap · {self._title}"
        self.query_one("#others", OptionList).display = False
        self.run_worker(self._load, thread=True, group="recap", exit_on_error=False)

    def _load(self, index: int = 0) -> None:
        """In a thread: git can be slow on a big or remote-mounted repo."""
        notes = self._notes or recap.notes(self._dirs)
        git = recap.since(notes[index]) if notes else None
        self.app.call_from_thread(self._show, notes, index, git)

    def _show(self, notes: list[Note], index: int, git: GitState | None) -> None:
        self._notes = notes
        if not notes:
            self.query_one("#head", Static).update(Text(NO_NOTE))
            return
        note = notes[index]
        self.query_one("#head", Static).update(Text(self._head(note, git)))
        self.query_one("#note", Markdown).update(note.entry.body if note.entry else "")
        self.query_one("#since", Static).update(self._since(note, git))
        others = self.query_one("#others", OptionList)
        self._others = [i for i in range(len(notes)) if i != index]
        others.clear_options()
        others.add_options([Text(self._label(notes[i])) for i in self._others])
        others.display = bool(self._others)

    def _head(self, note: Note, git: GitState | None) -> str:
        if note.entry is None:
            return NO_NOTE
        branch = (git.branch if git else None) or note.entry.branch or "no branch"
        age = recap.ago(self._now - note.entry.when.timestamp())
        return f"{_display(note.top)} · {branch} · checkpoint {age}"

    def _since(self, note: Note, git: GitState | None) -> Text:
        if git is None:
            return Text("Not a git repository.")
        lines = []
        if note.entry is not None:
            n = len(git.commits)
            lines.append(f"Since then: {n} commit{'' if n == 1 else 's'}")
            lines += [f"  {c.sha} {c.subject}" for c in git.commits]
        lines.append(f"{git.changed} changed, {git.untracked} untracked")
        return Text("\n".join(lines))

    def _label(self, note: Note) -> str:
        if note.entry is None:
            return f"{_display(note.top)} · no checkpoint"
        age = recap.ago(self._now - note.entry.when.timestamp())
        return f"{_display(note.top)} · {note.entry.branch or 'no branch'} · {age}"

    @on(OptionList.OptionSelected, "#others")
    def _open_other(self, event: OptionList.OptionSelected) -> None:
        index = self._others[event.option_index]
        self.run_worker(lambda: self._load(index), thread=True, group="recap", exit_on_error=False)
