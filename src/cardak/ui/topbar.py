"""The deck's top bar (#121): version and counts on the left, the plan limits on the right.

Too narrow for both on one line, the limits go to a second line under the counts.
"""

from rich.text import Text
from textual.widgets import Static


class TopBar(Static):
    DEFAULT_CSS = """
    TopBar { height: auto; padding: 0 1; background: $panel; color: $foreground; }
    """

    def __init__(self, *, id: str | None = None) -> None:  # noqa: A002
        super().__init__("", id=id)
        self._left = Text()
        self._right: Text | None = None

    def show(self, left: Text, right: Text | None) -> None:
        self._left, self._right = left, right
        self._lay_out()

    def on_resize(self) -> None:
        self._lay_out()

    def _lay_out(self) -> None:
        right = self._right
        if right is None:
            self.update(self._left)
            return
        width = self.content_size.width
        gap = width - self._left.cell_len - right.cell_len
        if gap >= 3:
            self.update(Text.assemble(self._left, " " * gap, right))
        else:
            self.update(Text.assemble(self._left, "\n", right))
