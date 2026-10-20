import pytest

from cardak.collectors.wezterm import list_panes


@pytest.mark.live
def test_real_wezterm_lists_panes_with_ttys() -> None:
    panes = list_panes()
    assert panes
    assert all(p.tty for p in panes)
