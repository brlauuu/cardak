from pathlib import Path

from cardak.core.agents import FocusChange
from cardak.core.focus import FOCUS_HEADER, focus_path, focused_pane, read_history


def test_focus_path() -> None:
    assert focus_path({"XDG_STATE_HOME": "/s"}) == Path("/s/cardak/focus")


def test_read_history(tmp_path: Path) -> None:
    f = tmp_path / "focus"
    f.write_text(f"{FOCUS_HEADER}\n7 1000\nnone 2500\ngarbage\n8 x\n9 3000\n")
    assert read_history(f) == [FocusChange(7, 1.0), FocusChange(None, 2.5), FocusChange(9, 3.0)]


def test_wrong_header_or_missing_file(tmp_path: Path) -> None:
    f = tmp_path / "focus"
    f.write_text("# cardak-focus: 2\n7 1000\n")
    assert read_history(f) == []
    assert read_history(tmp_path / "missing") == []
    f.write_bytes(b"\xff\xfe")
    assert read_history(f) == []


def test_focused_pane() -> None:
    assert focused_pane([]) is None
    assert focused_pane([FocusChange(7, 1.0), FocusChange(None, 2.0)]) is None
    assert focused_pane([FocusChange(None, 1.0), FocusChange(7, 2.0)]) == 7
