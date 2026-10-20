"""The deck: a live tree of WezTerm panes grouped by project."""

from pathlib import Path
from typing import Protocol

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.markup import escape
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Input, Label, Static, Tree
from textual.widgets.tree import TreeNode
from textual.worker import Worker

from cardak.core.config import ConfigError
from cardak.core.filter import filter_snapshot
from cardak.core.navigate import JumpResult
from cardak.core.snapshot import DeckSnapshot, Group, PaneRow
from cardak.ui.widgets import group_label, no_match_label, pane_label, unreachable_label

NodeData = tuple[Group, PaneRow | None]


class DeckSource(Protocol):
    @property
    def can_add(self) -> bool: ...

    def poll(self) -> DeckSnapshot: ...

    def add_project(self, name: str, path: Path) -> None: ...


class Jumper(Protocol):
    def jump(self, pane_id: int) -> JumpResult: ...


def node_id(data: NodeData | None) -> str | None:
    if data is None:
        return None
    group, row = data
    return f"p:{row.pane_id}" if row else f"g:{group.kind}:{group.key}"


def _cursor_target(
    nodes: dict[str, TreeNode[NodeData]], selected: str | None, focus_pane: int | None
) -> TreeNode[NodeData] | None:
    """Where the cursor goes after a rebuild: the best match while searching, else where it was."""
    if focus_pane is None:
        return nodes.get(selected) if selected else None
    leaf = next(
        (n for n in nodes.values() if n.data and n.data[1] and n.data[1].pane_id == focus_pane),
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


class DeckApp(App[None]):
    TITLE = "cardak"
    CSS = """
    AddProjectScreen { align: center middle; }
    #dialog { width: 70; height: auto; border: round $accent; padding: 1 2; background: $surface; }
    #find { display: none; }
    #banner { display: none; background: $error; color: $text; padding: 0 1; }
    """
    BINDINGS = [
        Binding("a", "add_project", "Add as project"),
        Binding("slash", "find", "Find"),
        Binding("escape", "clear_find", "Clear find", show=False),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("r", "refresh", "Refresh"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(
        self, source: DeckSource, navigator: Jumper | None = None, interval: float = 2.0
    ) -> None:
        super().__init__()
        self.source = source
        self.navigator = navigator
        self.interval = interval
        self.raw: DeckSnapshot | None = None
        self.snapshot: DeckSnapshot | None = None
        self.find_query = ""
        self._best: int | None = None
        self._collapsed: set[str] = set()
        self._poll_worker: Worker[None] | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("", id="banner")
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
            snap = DeckSnapshot(error=f"internal error: {e!r}")
        self.call_from_thread(self.apply_snapshot, snap)

    def apply_snapshot(self, snap: DeckSnapshot) -> None:
        if snap == self.raw:
            return
        self.raw = snap
        banner = self.query_one("#banner", Static)
        banner.update(escape(f"projects.toml: {snap.config_error}") if snap.config_error else "")
        banner.display = snap.config_error is not None
        self._render(follow_best=False)

    def _render(self, follow_best: bool) -> None:
        raw = self.raw
        if raw is None:
            return
        result = filter_snapshot(raw, self.find_query)
        self.snapshot = result.snapshot
        self._best = result.best_pane_id
        query = self.find_query.strip()
        if query:
            self.sub_title = f'{result.n_matches} of {raw.n_panes} panes match "{query}"'
        else:
            self.sub_title = f"{raw.n_projects} projects · {raw.n_panes} panes"
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
        if snap.error:
            tree.root.add_leaf(unreachable_label(snap.error))
            return
        if not snap.groups and empty_query:
            tree.root.add_leaf(no_match_label(empty_query))
            return
        nodes: dict[str, TreeNode[NodeData]] = {}
        for group in snap.groups:
            data: NodeData = (group, None)
            gid = node_id(data) or ""
            node = tree.root.add(group_label(group), data=data, expand=gid not in self._collapsed)
            nodes[gid] = node
            for row in group.panes:
                leaf_data: NodeData = (group, row)
                nodes[node_id(leaf_data) or ""] = node.add_leaf(pane_label(row), data=leaf_data)
        target = _cursor_target(nodes, selected, focus_pane)
        if target is not None:
            # New nodes get their line numbers on the next refresh; moving now lands on line 0.
            self.call_after_refresh(tree.move_cursor, target)
        else:
            # clear() leaves the cursor on no line, so the first key would only "find" row 0.
            self.call_after_refresh(self._cursor_to_first_row)

    def _cursor_to_first_row(self) -> None:
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
        group, row = node.data
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
        if data is not None and data[1] is not None:  # a group row toggles itself (auto_expand)
            self.jump(data[1].pane_id)

    def jump(self, pane_id: int) -> None:
        navigator = self.navigator
        if navigator is None:
            return

        def work() -> None:
            try:
                result = navigator.jump(pane_id)
            except Exception as e:  # a navigator bug must not take the deck down
                result = JumpResult("failed", f"Couldn't jump: {e!r}")
            self.call_from_thread(self._report_jump, result)

        self.run_worker(work, thread=True, group="jump", exit_on_error=False)

    def _report_jump(self, result: JumpResult) -> None:
        if result.message:
            severity = "error" if result.kind == "failed" else "information"
            self.notify(escape(result.message), severity=severity)
