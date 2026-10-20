"""The deck: a live tree of WezTerm panes grouped by project."""

from pathlib import Path
from typing import Protocol

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Input, Label, Static, Tree
from textual.widgets.tree import TreeNode

from cardak.config import ConfigError
from cardak.snapshot import DeckSnapshot, Group, PaneRow
from cardak.ui.widgets import group_label, pane_label, unreachable_label

NodeData = tuple[Group, PaneRow | None]


class DeckSource(Protocol):
    @property
    def can_add(self) -> bool: ...

    def poll(self) -> DeckSnapshot: ...

    def add_project(self, name: str, path: Path) -> None: ...


def node_id(data: NodeData | None) -> str | None:
    if data is None:
        return None
    group, row = data
    return f"p:{row.pane_id}" if row else f"g:{group.kind}:{group.key}"


class AddProjectScreen(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, path: Path, default_name: str) -> None:
        super().__init__()
        self._path = path
        self._default_name = default_name

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(f"Add {self._path} as a project. Name:")
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
    #banner { display: none; background: $error; color: $text; padding: 0 1; }
    """
    BINDINGS = [
        Binding("a", "add_project", "Add as project"),
        Binding("r", "refresh", "Refresh"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, source: DeckSource, interval: float = 2.0) -> None:
        super().__init__()
        self.source = source
        self.interval = interval
        self.snapshot: DeckSnapshot | None = None
        self._collapsed: set[str] = set()

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("", id="banner")
        yield Tree[NodeData]("projects", id="tree")
        yield Footer()

    @property
    def deck_tree(self) -> Tree[NodeData]:
        return self.query_one("#tree", Tree)

    def on_mount(self) -> None:
        self.deck_tree.show_root = False
        self.action_refresh()
        self.set_interval(self.interval, self.action_refresh)

    def action_refresh(self) -> None:
        self.run_worker(self._poll, thread=True, exclusive=True, group="poll", exit_on_error=False)

    def _poll(self) -> None:
        try:
            snap = self.source.poll()
        except Exception as e:  # a collector bug must not take the deck down
            snap = DeckSnapshot(error=f"internal error: {e!r}")
        self.call_from_thread(self.apply_snapshot, snap)

    def apply_snapshot(self, snap: DeckSnapshot) -> None:
        if snap == self.snapshot:
            return
        self.snapshot = snap
        self.sub_title = f"{snap.n_projects} projects · {snap.n_panes} panes"
        banner = self.query_one("#banner", Static)
        banner.update(f"projects.toml: {snap.config_error}" if snap.config_error else "")
        banner.display = snap.config_error is not None
        self._rebuild(snap)

    def _rebuild(self, snap: DeckSnapshot) -> None:
        tree = self.deck_tree
        cursor = tree.cursor_node
        selected = node_id(cursor.data) if cursor else None
        tree.clear()
        if snap.error:
            tree.root.add_leaf(unreachable_label(snap.error))
            return
        target: TreeNode[NodeData] | None = None
        for group in snap.groups:
            data: NodeData = (group, None)
            node = tree.root.add(
                group_label(group), data=data, expand=node_id(data) not in self._collapsed
            )
            if node_id(data) == selected:
                target = node
            for row in group.panes:
                leaf = node.add_leaf(pane_label(row), data=(group, row))
                if node_id((group, row)) == selected:
                    target = leaf
        if target is not None:
            # New nodes get their line numbers on the next refresh; moving now lands on line 0.
            self.call_after_refresh(tree.move_cursor, target)

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
                self.notify(str(e), severity="error")
                return
            self.notify(f"Added {name}")
            self.action_refresh()

        self.push_screen(AddProjectScreen(path, path.name), done)
