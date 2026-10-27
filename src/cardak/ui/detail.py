"""The detail panel (`i` on a Claude row) and the transcript view (`t` in the panel).

The panel quotes and counts what the hooks recorded; nothing in it is generated.
"""

import json
import time
from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.markup import escape
from textual.screen import Screen
from textual.widgets import Button, Collapsible, Footer, Header, Static

from cardak.collectors.transcripts import Entry, read_transcript
from cardak.core.agents import KIND_LABELS, SessionState, tool_summary
from cardak.core.detail import shown_path, turn_text, words
from cardak.core.summary import SummaryInfo
from cardak.ui.widgets import IDLE, WAITING, WORKING, age

PAGE = 200


def _needs(text: Text, state: SessionState, summary: str | None) -> None:
    text.append("NEEDS  ", style="bold")
    if state.status == "waiting" and state.kind:
        text.append(f"{KIND_LABELS[state.kind]}\n", style=f"bold {WAITING}")
        if summary:
            text.append(f"  Summary: {summary}\n")
        keep = "start" if state.kind == "permission" else "end"
        text.append(f"  {words(state.reason or '', keep)}\n")
    elif state.status == "working":
        text.append(f"Working: {state.current_tool or '…'}\n")
    else:
        text.append("Nothing\n")


def detail_text(
    state: SessionState, project: str, now: float, info: SummaryInfo | None = None
) -> Text:
    text = Text()
    color = {"waiting": WAITING, "working": WORKING}.get(state.status, IDLE)
    text.append("● " if state.status != "idle" else "○ ", style=f"bold {color}")
    text.append(f"{project} · claude", style="bold")
    text.append(f"  {state.status} {age(now - state.status_since)}\n\n", style=color)

    _needs(text, state, info.text if info is not None else None)

    if state.last_prompt:
        asked = f"  ({age(now - state.last_prompt_at)} ago)" if state.last_prompt_at else ""
        text.append("\nYOU ASKED", style="bold")
        text.append(f"{asked}\n", style="dim")
        text.append(f'  "{words(state.last_prompt, "start")}"\n')

    text.append("\nTHIS TURN  ", style="bold")
    text.append(f"{turn_text(state)}\n")
    for path in state.turn_files:
        text.append("  edited ", style="green")
        text.append(f"{shown_path(path, state.cwd)}\n")
    if info is not None:
        text.append(f"\nSummaries this month: ${info.month_spend:.2f} of ${info.limit:.2f}\n")
    text.rstrip()
    return text


class DetailScreen(Screen[int | None]):
    """Dismisses with the pane id to jump to, or None."""

    BINDINGS = [
        Binding("enter", "jump", "Jump to pane"),
        Binding("t", "transcript", "Full transcript"),
        Binding("escape", "back", "Back"),
    ]

    def __init__(
        self,
        state: SessionState,
        project: str,
        info: SummaryInfo | None = None,
        pane_id: int | None = None,  # the row's pane: a session matched by pid knows none
    ) -> None:
        super().__init__()
        self.state = state
        self.pane_id = pane_id
        self.project = project
        self.info = info

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll():
            yield Static(detail_text(self.state, self.project, time.time(), self.info), id="detail")
        yield Footer()

    def action_jump(self) -> None:
        self.dismiss(self.pane_id if self.pane_id is not None else self.state.pane_id)

    def action_back(self) -> None:
        self.dismiss(None)

    def action_transcript(self) -> None:
        path = self.state.transcript_path
        if path is None:
            self.notify("No transcript recorded for this session.", severity="warning")
            return
        self.app.push_screen(TranscriptScreen(path, f"{self.project} · claude"))


def _entry_widget(entry: Entry) -> Static | Collapsible:
    if entry.kind == "tool":
        summary = tool_summary(entry.tool_name or "tool", entry.tool_input)
        body = json.dumps(dict(entry.tool_input), indent=2, ensure_ascii=False)
        if entry.text:
            body += f"\n\n{entry.text}"
        # titles are markup: a command like `grep "[/]"` must stay literal text
        return Collapsible(Static(Text(body)), title=escape(summary), collapsed=True)
    if entry.kind == "thinking":
        return Collapsible(Static(Text(entry.text)), title="thinking", collapsed=True)
    who = "you" if entry.kind == "user" else "claude"
    label = Text(f"{who}\n", style="bold cyan" if who == "you" else "bold")
    return Static(label + Text(entry.text), classes=f"message {entry.kind}")


class TranscriptScreen(Screen[None]):
    BINDINGS = [Binding("escape", "app.pop_screen", "Back"), Binding("r", "reload", "Reload")]
    CSS = """
    TranscriptScreen .message { margin: 1 0 0 0; }
    #earlier { display: none; }
    """

    def __init__(self, path: Path, title: str) -> None:
        super().__init__()
        self.path = path
        self.title_text = title
        self.limit = PAGE

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll():
            yield Button("load earlier", id="earlier")
            yield VerticalScroll(id="entries")
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = self.title_text
        self.action_reload()

    def action_reload(self) -> None:
        self.run_worker(
            self._load, thread=True, group="transcript", exclusive=True, exit_on_error=False
        )

    def _load(self) -> None:
        entries, earlier = read_transcript(self.path, self.limit)
        self.app.call_from_thread(self._show, entries, earlier)

    async def _show(self, entries: list[Entry], earlier: int) -> None:
        button = self.query_one("#earlier", Button)
        button.label = f"load earlier ({earlier})"
        button.display = earlier > 0
        box = self.query_one("#entries", VerticalScroll)
        await box.remove_children()
        if not entries:
            await box.mount(Static(Text("No messages yet.", style="dim italic")))
            return
        await box.mount_all([_entry_widget(e) for e in entries])

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "earlier":
            self.limit += PAGE
            self.action_reload()
