from pathlib import Path

from cardak.core.cleanup import COMMAND, NOT_A_PROJECT, Place, place
from cardak.core.snapshot import Group, PaneRow

KEY = Path("/home/user/repos/webshop")


def row(pane_id: int, is_self: bool = False) -> PaneRow:
    return PaneRow(
        pane_id=pane_id, label="zsh", detail=None, cwd=None, cwd_display=".", cpu_pct=None,
        rss=0, is_self=is_self,
    )  # fmt: skip


def group(kind: str, *rows: PaneRow, key: Path | None = KEY) -> Group:
    return Group(
        kind=kind, key=key, name="webshop", accent=None, path=None,  # type: ignore[arg-type]
        cpu_pct=0.0, rss=0, panes=rows,
    )  # fmt: skip


def test_command_runs_the_skill() -> None:
    assert COMMAND == "claude /repo-cleanup"


def test_project_row_opens_next_to_its_first_pane() -> None:
    assert place(group("config", row(4), row(9)), None) == Place(KEY, 4)
    assert place(group("git", row(4)), None) == Place(KEY, 4)


def test_selected_pane_picks_the_window() -> None:
    assert place(group("config", row(4), row(9)), row(9)) == Place(KEY, 9)


def test_project_without_panes_opens_in_the_decks_window() -> None:
    assert place(group("config"), None) == Place(KEY, None)


def test_other_and_waiting_are_not_projects() -> None:
    assert place(group("other", row(4), key=None), None) == NOT_A_PROJECT
    assert place(group("waiting", row(4), key=None), row(4)) == NOT_A_PROJECT
