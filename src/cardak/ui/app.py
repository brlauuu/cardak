"""The deck: a live tree of WezTerm panes grouped by project."""

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.content import Content
from textual.markup import escape
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Input, Label, Static, Tree
from textual.widgets.tree import TreeNode
from textual.worker import Worker

from cardak.core import checkpoint, recap
from cardak.core.agents import SessionState
from cardak.core.config import ConfigError
from cardak.core.eventlog import sentences
from cardak.core.filter import filter_snapshot, waiting_only
from cardak.core.navigate import JumpResult
from cardak.core.snapshot import DeckSnapshot, DockerLine, Group, PaneRow
from cardak.core.store import DayPeak, ResourceEvent, StoreError
from cardak.core.summary import SummaryInfo
from cardak.core.themes import Scheme, SchemeStore
from cardak.ui.confirm import ConfirmScreen
from cardak.ui.detail import DetailScreen
from cardak.ui.events import EventLogScreen
from cardak.ui.history import HistoryScreen
from cardak.ui.recap import RecapScreen
from cardak.ui.theme_picker import ThemePickerScreen, ThemePreviewer
from cardak.ui.widgets import (
    docker_label,
    group_label,
    internal_label,
    machine_label,
    mcp_label,
    no_match_label,
    nothing_waiting_label,
    outside_label,
    pane_label,
    reason_label,
    unreachable_label,
    waiting_label,
)

LOG_DAYS = 30
SENT_WITH_IT = "Anything already typed in that pane's input is sent with it."


@dataclass(frozen=True)
class Node:
    """What a tree line shows: a group, a pane in it, or a line under one of them."""

    group: Group
    row: PaneRow | None = None
    role: Literal["group", "pane", "reason", "mcp", "docker", "outside"] = "group"
    line: DockerLine | None = None


NodeData = Node


class DeckSource(Protocol):
    @property
    def can_add(self) -> bool: ...

    def poll(self) -> DeckSnapshot: ...

    def add_project(self, name: str, path: Path) -> None: ...

    def set_theme(self, project_path: Path, theme: str, name: str | None = None) -> None: ...

    def regenerate(self) -> None: ...

    def mark_seen_pane(self, pane_id: int) -> None: ...

    def session(self, session_id: str) -> SessionState | None: ...

    def summary_info(self, state: SessionState) -> SummaryInfo | None: ...


class ResourceLog(Protocol):
    """What the deck reads from cardak.db for the event log, the crash banner and history."""

    def resource_events_since(self, ts: float) -> list[ResourceEvent]: ...

    def latest_crash(self, since: float) -> ResourceEvent | None: ...

    def dismiss_report(self, ts: float) -> None: ...

    def history_days(self, since: float) -> list[DayPeak]: ...

    def history_medians(self, since: float) -> dict[str, tuple[float, float]]: ...

    def history_hours(self, subject: str, since: float) -> list[tuple[int, int, float]]: ...

    def alert_counts(self, since: float) -> dict[str, int]: ...


class Jumper(Protocol):
    def jump(self, pane_id: int) -> JumpResult: ...


def node_id(data: NodeData | None) -> str | None:
    if data is None:
        return None
    group = f"{data.group.kind}:{data.group.key}"
    if data.role == "docker" and data.line is not None:
        return f"d:{group}:{data.line.label}"
    if data.role == "outside":
        return f"o:{group}"
    if data.row is None:
        return f"g:{group}"
    if data.role == "reason":
        return f"r:{data.row.pane_id}"
    if data.role == "mcp":
        return f"m:{data.row.pane_id}"
    # the same pane can be listed twice: under "Waiting on you" and in its project
    return f"{'w' if data.group.kind == 'waiting' else 'p'}:{data.row.pane_id}"


def _cursor_target(
    nodes: dict[str, TreeNode[NodeData]], selected: str | None, focus_pane: int | None
) -> TreeNode[NodeData] | None:
    """Where the cursor goes after a rebuild: the best match while searching, else where it was."""
    if focus_pane is None:
        if not selected:
            return None
        if selected not in nodes and selected[:2] in ("w:", "r:"):
            selected = "p:" + selected[2:]  # it left "Waiting on you": its row in the project
        return nodes.get(selected)
    leaf = next(
        (n for n in nodes.values() if n.data and n.data.row and n.data.row.pane_id == focus_pane),
        None,
    )
    if leaf is None or leaf.parent is None or leaf.parent.is_expanded:
        return leaf
    return leaf.parent  # a leaf in a collapsed group has no line; land on its group


class AddProjectScreen(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, path: Path, default_name: str) -> None:
        super().__init__()
        self._path = path
        self._default_name = default_name

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(f"Add {escape(str(self._path))} as a project. Name:")
            yield Input(value=self._default_name, id="name")

    @on(Input.Submitted)
    def _submit(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)

    def action_cancel(self) -> None:
        self.dismiss(None)


def _leaves(group: Group, now: float) -> list[tuple[Text, Node]]:
    """A group's lines: one per pane, plus a reason line under each waiting pane in a project."""
    if group.kind == "waiting":
        return [(waiting_label(row, now), Node(group, row, "pane")) for row in group.panes]
    lines: list[tuple[Text, Node]] = []
    for row in group.panes:
        lines.append((pane_label(row, now), Node(group, row, "pane")))
        if row.mcp_rss > 0:
            lines.append((mcp_label(row), Node(group, row, "mcp")))
        if row.agent and row.agent.status == "waiting":
            lines.append((reason_label(row), Node(group, row, "reason")))
    return lines + _resource_lines(group)


def _resource_lines(group: Group) -> list[tuple[Text, Node]]:
    """Docker work and, under Other, what runs outside WezTerm."""
    lines = [(docker_label(d), Node(group, role="docker", line=d)) for d in group.docker]
    if group.outside_rss is not None:
        lines.append((outside_label(group), Node(group, role="outside")))
    return lines


class DeckApp(App[None]):
    TITLE = "cardak"
    CSS = """
    AddProjectScreen { align: center middle; }
    #dialog { width: 70; height: auto; border: round $accent; padding: 1 2; background: $surface; }
    #find { display: none; }
    #banner.warning { background: $warning; }
    #banner { display: none; background: $error; color: $text; padding: 0 1; }
    #crash { display: none; background: $error-darken-1; color: $text; padding: 0 1; }
    #machine { display: none; padding: 0 1; }
    """
    BINDINGS = [
        Binding("a", "add_project", "Add as project"),
        Binding("t", "theme", "Theme"),
        Binding("w", "waiting_only", "Waiting"),
        Binding("i", "detail", "Details"),
        Binding("h", "history", "History"),
        Binding("e", "event_log", "Events"),
        Binding("d", "dismiss_crash", "Dismiss", show=False),
        Binding("slash", "find", "Find"),
        Binding("escape", "clear_find", "Clear find", show=False),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("c", "checkpoint", "Checkpoint"),
        Binding("r", "recap", "Recap"),
        Binding("R", "refresh", "Refresh", show=False),
        Binding("q", "quit", "Quit"),
    ]

    # Keys that act on the tree: off (and out of the footer) while another screen is on top.
    DECK_ACTIONS = frozenset(
        {"add_project", "theme", "waiting_only", "detail", "find", "refresh", "cursor_down",
         "cursor_up", "clear_find", "event_log", "dismiss_crash", "history", "checkpoint",
         "recap"}
    )  # fmt: skip

    def format_title(self, title: str, sub_title: str) -> Content:
        """The header, with the `·` the rest of the deck uses (Textual's default is `—`)."""
        if not sub_title:
            return Content(title)
        return Content.assemble(Content(title), (" · ", "dim"), Content(sub_title).stylize("dim"))

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        return not (action in self.DECK_ACTIONS and len(self.screen_stack) > 1)

    def __init__(
        self,
        source: DeckSource,
        navigator: Jumper | None = None,
        interval: float = 2.0,
        previewer: ThemePreviewer | None = None,
        schemes: SchemeStore | None = None,
        load_schemes: Callable[[], Mapping[str, Scheme]] | None = None,
        theme_status: Callable[[], str | None] | None = None,
        hooks_status: Callable[[], str | None] | None = None,
        resources_status: Callable[[], str | None] | None = None,
        log: ResourceLog | None = None,
        send: Callable[[int, str], None] | None = None,
        skills_status: Callable[[], str | None] | None = None,
    ) -> None:
        super().__init__()
        self.source = source
        self.navigator = navigator
        self.previewer = previewer
        self.schemes = schemes
        self.load_schemes = load_schemes
        self.theme_status = theme_status  # how to enable themes, when they aren't yet
        self.hooks_status = hooks_status  # how to enable agent status, when it isn't yet
        self.resources_status = resources_status  # how to enable alerts, when they aren't yet
        self.send = send  # types into a pane: only /checkpoint, only after asking
        self.skills_status = skills_status  # how to install /checkpoint, when it isn't
        self.resource_log = log  # its own cardak.db connection: events, crash banner, history
        self._crash: ResourceEvent | None = None
        self._cap_noticed = False  # the summary-limit notice shows once per run
        self.waiting_only = False
        self.interval = interval
        self.raw: DeckSnapshot | None = None
        self.snapshot: DeckSnapshot | None = None
        self.find_query = ""
        self._best: int | None = None
        self._follow_pending = False  # a move to the best match is queued for after refresh
        self._collapsed: set[str] = set()
        self._poll_worker: Worker[None] | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("", id="machine")
        yield Static("", id="banner")
        yield Static("", id="crash")
        yield Input(placeholder="find: project, process, title or path", id="find")
        yield Tree[NodeData]("projects", id="tree")
        yield Footer()

    @property
    def deck_tree(self) -> Tree[NodeData]:
        return self.query_one("#tree", Tree)

    def on_mount(self) -> None:
        self.deck_tree.show_root = False
        # AUTO_FOCUS would pick the hidden #find input, which then swallows every key.
        self.deck_tree.focus()
        self.action_refresh()
        self.set_interval(self.interval, self.action_refresh)
        for status in (self.hooks_status, self.resources_status):
            if status is not None and (hint := status()):
                self.notify(escape(hint), severity="warning")
        self._show_crash()
        if self.schemes is not None and not self.schemes.get() and self.load_schemes is not None:
            self.run_worker(self._load_schemes, thread=True, group="schemes", exit_on_error=False)

    def _show_crash(self) -> None:
        """The last loud crash report, until dismissed: the deck may be the first thing reopened."""
        if self.resource_log is None:
            return
        try:
            self._crash = self.resource_log.latest_crash(time.time() - LOG_DAYS * 86_400)
        except StoreError:
            return
        if self._crash is None:
            return
        banner = self.query_one("#crash", Static)
        what = " ".join(sentences(self._crash.text)[:2])  # when, and why: out of memory
        banner.update(escape(f"{what} · d: dismiss · e: event log"))
        banner.display = True

    def action_dismiss_crash(self) -> None:
        if self._crash is None or self.resource_log is None:
            return
        try:
            self.resource_log.dismiss_report(self._crash.ts)
        except StoreError as e:
            self.notify(escape(str(e)), severity="error")
            return
        self._crash = None
        self.query_one("#crash", Static).display = False

    def action_history(self) -> None:
        if self.resource_log is None:
            self.notify("History unavailable.", severity="warning")
            return
        self.push_screen(HistoryScreen(self.resource_log, self.raw, time.time()))

    def action_event_log(self) -> None:
        if self.resource_log is None:
            self.notify("Event log unavailable.", severity="warning")
            return
        try:
            events = self.resource_log.resource_events_since(time.time() - LOG_DAYS * 86_400)
        except StoreError as e:
            self.notify(escape(f"Event log unavailable: {e}"), severity="error")
            return
        self.push_screen(EventLogScreen(events))

    def action_refresh(self) -> None:
        # Thread workers cannot be cancelled, so never start a poll while one is running:
        # two concurrent samples would skew cpu_percent and could apply snapshots out of order.
        if self._poll_worker is not None and not self._poll_worker.is_finished:
            return
        self._poll_worker = self.run_worker(
            self._poll, thread=True, group="poll", exit_on_error=False
        )

    def _poll(self) -> None:
        try:
            snap = self.source.poll()
        except Exception as e:  # a collector bug must not take the deck down
            snap = DeckSnapshot(error=repr(e), internal=True)
        self.call_from_thread(self.apply_snapshot, snap)

    def apply_snapshot(self, snap: DeckSnapshot) -> None:
        if snap == self.raw:
            return
        self.raw = snap
        machine = self.query_one("#machine", Static)
        if snap.machine_view is not None:
            machine.update(machine_label(snap.machine_view))
        machine.display = snap.machine_view is not None
        banner = self.query_one("#banner", Static)
        if snap.config_error:
            banner.update(escape(snap.config_error))  # ConfigError names the file itself
        else:
            banner.update(escape("\n".join(snap.config_warnings)))
        banner.display = bool(snap.config_error or snap.config_warnings)
        banner.set_class(snap.config_error is None and bool(snap.config_warnings), "warning")
        if snap.summary_capped and not self._cap_noticed:
            self._cap_noticed = True
            self.notify(
                escape(
                    f"Summary limit of ${snap.summary_limit:.2f} reached for this month; showing "
                    "raw reasons. Change it in [summary] in projects.toml."
                ),
                severity="warning",
            )
        self._render(follow_best=False)

    def _render(self, follow_best: bool) -> None:
        raw = self.raw
        if raw is None:
            return
        # a poll in the same frame as a keystroke must not undo the move to the best match
        follow_best = follow_best or self._follow_pending
        self._follow_pending = follow_best
        shown = waiting_only(raw) if self.waiting_only else raw
        result = filter_snapshot(shown, self.find_query)
        self.snapshot = result.snapshot
        self._best = result.best_pane_id
        query = self.find_query.strip()
        if query:
            self.sub_title = f'{result.n_matches} of {raw.n_panes} panes match "{query}"'
        else:
            self.sub_title = f"{raw.n_projects} projects · {raw.n_panes} panes"
        if raw.n_waiting:
            self.sub_title += f" · {raw.n_waiting} waiting"
        self._rebuild(
            result.snapshot,
            focus_pane=result.best_pane_id if follow_best else None,
            empty_query=query or None,
        )

    def _rebuild(
        self, snap: DeckSnapshot, focus_pane: int | None = None, empty_query: str | None = None
    ) -> None:
        tree = self.deck_tree
        cursor = tree.cursor_node
        selected = node_id(cursor.data) if cursor else None
        tree.clear()
        if (message := self._empty_label(snap, empty_query)) is not None:
            tree.root.add_leaf(message)
            return
        now = time.time()
        nodes: dict[str, TreeNode[NodeData]] = {}
        for group in snap.groups:
            data = Node(group)
            gid = node_id(data) or ""
            node = tree.root.add(group_label(group), data=data, expand=gid not in self._collapsed)
            nodes[gid] = node
            for label, leaf_data in _leaves(group, now):
                nodes[node_id(leaf_data) or ""] = node.add_leaf(label, data=leaf_data)
        target = _cursor_target(nodes, selected, focus_pane)
        if target is not None:
            # New nodes get their line numbers on the next refresh; moving now lands on line 0.
            self.call_after_refresh(self._move_cursor, target)
        else:
            # clear() leaves the cursor on no line, so the first key would only "find" row 0.
            self.call_after_refresh(self._cursor_to_first_row)

    def _move_cursor(self, target: TreeNode[NodeData]) -> None:
        self._follow_pending = False
        self.deck_tree.move_cursor(target)

    def _empty_label(self, snap: DeckSnapshot, empty_query: str | None) -> Text | None:
        """What the tree shows instead of groups, if anything."""
        if snap.error:
            return internal_label(snap.error) if snap.internal else unreachable_label(snap.error)
        if not snap.groups and empty_query:
            return no_match_label(empty_query)
        if not snap.groups and self.waiting_only:
            return nothing_waiting_label()
        return None

    def action_detail(self) -> None:
        node = self.deck_tree.cursor_node
        row = node.data.row if node is not None and node.data is not None else None
        if row is None or not row.is_claude:
            self.notify("Select a Claude session.", severity="warning")
            return
        state = self.source.session(row.agent.session_id) if row.agent else None
        if state is None:
            self.notify(
                "No status recorded for this session yet (sessions started before "
                "`cardak hooks install` report nothing).",
                severity="warning",
            )
            return
        project = row.agent.project if row.agent else ""

        def done(pane_id: int | None) -> None:
            if pane_id is not None:
                self.jump(pane_id)

        info = self.source.summary_info(state)
        self.push_screen(DetailScreen(state, project, info, row.pane_id), done)

    def action_checkpoint(self) -> None:
        node = self.deck_tree.cursor_node
        send = self.send
        if send is None or node is None or node.data is None:
            return
        if self.skills_status is not None and (hint := self.skills_status()):
            self.notify(escape(hint), severity="warning")
            return
        found = checkpoint.target(node.data.group, node.data.row)
        if isinstance(found, str):
            self.notify(escape(found), severity="warning")
            return
        row = found
        project = row.agent.project if row.agent else node.data.group.name
        session_id = row.agent.session_id if row.agent else ""

        def done(yes: bool | None) -> None:
            if not yes:
                return
            # the dialog can stay open while Claude moves on: decide again on what's stored now
            if reason := checkpoint.recheck(self.source.session(session_id), row.pane_id):
                self.notify(escape(reason), severity="warning")
                return
            self.run_worker(
                lambda: self._send(send, row.pane_id), thread=True, group="send",
                exit_on_error=False,
            )  # fmt: skip

        question = f"Send /checkpoint to pane {row.pane_id} · {project} · {row.label}?"
        self.push_screen(ConfirmScreen(question, SENT_WITH_IT), done)

    def _send(self, send: Callable[[int, str], None], pane_id: int) -> None:
        try:
            send(pane_id, checkpoint.TEXT)
            time.sleep(checkpoint.ENTER_DELAY_S)
            send(pane_id, checkpoint.ENTER)
        except Exception as e:  # a WezTerm failure (or any bug) is a notice, not a crash
            self.call_from_thread(self.notify, escape(f"Couldn't send: {e}"), severity="error")
            return
        self.call_from_thread(self.notify, f"Sent /checkpoint to pane {pane_id}.")
        self.call_from_thread(self.action_refresh)

    def action_recap(self) -> None:
        node = self.deck_tree.cursor_node
        if node is None or node.data is None:
            return
        dirs = recap.dirs_for(node.data.group, node.data.row)
        if isinstance(dirs, str):
            self.notify(dirs, severity="warning")
            return
        self.push_screen(RecapScreen(node.data.group.name, dirs, time.time()))

    def action_waiting_only(self) -> None:
        self.waiting_only = not self.waiting_only
        self._render(follow_best=False)

    def _cursor_to_first_row(self) -> None:
        self._follow_pending = False
        tree = self.deck_tree
        if tree.cursor_line < 0 and tree.root.children:
            tree.cursor_line = 0

    def action_find(self) -> None:
        find = self.query_one("#find", Input)
        find.display = True
        find.focus()

    def action_clear_find(self) -> None:
        find = self.query_one("#find", Input)
        if not find.display:
            return
        find.value = ""
        find.display = False
        self.find_query = ""
        self._render(follow_best=False)
        self.deck_tree.focus()

    @on(Input.Changed, "#find")
    def _find_changed(self, event: Input.Changed) -> None:
        if event.value == self.find_query:
            return
        self.find_query = event.value
        self._render(follow_best=True)

    @on(Input.Submitted, "#find")
    def _find_submitted(self, event: Input.Submitted) -> None:
        if self._best is not None:
            self.jump(self._best)

    @on(Tree.NodeCollapsed)
    def _remember_collapsed(self, event: Tree.NodeCollapsed[NodeData]) -> None:
        if (nid := node_id(event.node.data)) is not None:
            self._collapsed.add(nid)

    @on(Tree.NodeExpanded)
    def _remember_expanded(self, event: Tree.NodeExpanded[NodeData]) -> None:
        if (nid := node_id(event.node.data)) is not None:
            self._collapsed.discard(nid)

    def _add_target(self) -> Path | None:
        node = self.deck_tree.cursor_node
        if node is None or node.data is None:
            return None
        group, row = node.data.group, node.data.row
        if group.kind == "git":
            return group.key
        if group.kind == "other" and row is not None:
            return row.cwd
        return None

    def action_add_project(self) -> None:
        path = self._add_target()
        if path is None:
            self.notify(
                "Select a repo marked 'not in config', or a pane in Other.", severity="warning"
            )
            return
        if not self.source.can_add:
            self.notify("Fix projects.toml before adding projects.", severity="error")
            return

        def done(name: str | None) -> None:
            if not name:
                return
            try:
                self.source.add_project(name, path)
            except ConfigError as e:
                self.notify(escape(str(e)), severity="error")
                return
            self.notify(f"Added {escape(name)}")
            self.action_refresh()

        self.push_screen(AddProjectScreen(path, path.name), done)

    def action_cursor_down(self) -> None:
        self.deck_tree.action_cursor_down()

    def action_cursor_up(self) -> None:
        self.deck_tree.action_cursor_up()

    @on(Tree.NodeSelected)
    def _selected(self, event: Tree.NodeSelected[NodeData]) -> None:
        data = event.node.data
        if data is not None and data.row is not None:  # a group row toggles itself (auto_expand)
            self.jump(data.row.pane_id)

    def jump(self, pane_id: int) -> None:
        navigator = self.navigator
        if navigator is None:
            return

        def work() -> None:
            try:
                result = navigator.jump(pane_id)
                if result.kind != "failed":
                    self.source.mark_seen_pane(pane_id)
            except Exception as e:  # a navigator bug must not take the deck down
                result = JumpResult("failed", f"Couldn't jump: {e!r}")
            self.call_from_thread(self._report_jump, result)
            if result.kind != "failed":
                self.call_from_thread(self.action_refresh)

        self.run_worker(work, thread=True, group="jump", exit_on_error=False)

    def _report_jump(self, result: JumpResult) -> None:
        if result.message:
            severity = "error" if result.kind == "failed" else "information"
            self.notify(escape(result.message), severity=severity)

    def _load_schemes(self) -> None:
        if self.load_schemes is None or self.schemes is None:
            return
        try:
            self.schemes.set(self.load_schemes())
        except Exception:  # the picker retries and shows the reason; accents just stay unset
            return
        self.source.regenerate()
        self.call_from_thread(self.action_refresh)

    def _schemes_for_picker(self) -> Mapping[str, Scheme]:
        if self.schemes is not None and self.schemes.get():
            return self.schemes.get()
        if self.load_schemes is None:
            raise RuntimeError("scheme list not available")
        schemes = self.load_schemes()
        if self.schemes is not None:
            self.schemes.set(schemes)
        return schemes

    def action_theme(self) -> None:
        node = self.deck_tree.cursor_node
        group = node.data.group if node is not None and node.data is not None else None
        if group is None or group.kind == "other" or group.key is None:
            self.notify("Select a project or a repo to theme.", severity="warning")
            return
        if not self.source.can_add:
            self.notify("Fix projects.toml before changing themes.", severity="error")
            return
        name, path = group.name, group.key

        def done(scheme: str | None) -> None:
            if not scheme:
                return
            try:
                self.source.set_theme(path, scheme, name)
            except ConfigError as e:
                self.notify(escape(str(e)), severity="error")
                return
            self.notify(escape(f"{name}: {scheme}"))
            if self.theme_status is not None and (hint := self.theme_status()):
                self.notify(escape(hint), severity="warning")
            self.action_refresh()

        self.push_screen(
            ThemePickerScreen(name, path, group.theme, self._schemes_for_picker, self.previewer),
            done,
        )
