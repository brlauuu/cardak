import sys
from pathlib import Path

from textual.widgets._footer import FooterKey

from cardak.ui.footer import WrappingFooter, key_width, row_sizes

sys.path.insert(0, str(Path(__file__).parent))
from test_app import SNAP, DeckApp, StubSource, settle  # noqa: E402


def test_everything_fits_on_one_row() -> None:
    assert row_sizes([10, 10, 10], 30) == (3,)


def test_rows_are_evened_out() -> None:
    # Filling rows in turn would leave one key on the second row.
    assert row_sizes([10] * 7, 60) == (4, 3)


def test_as_many_rows_as_needed() -> None:
    assert row_sizes([10] * 9, 30) == (3, 3, 3)


def test_a_key_wider_than_the_screen_gets_its_own_row() -> None:
    assert row_sizes([5, 50, 5], 20) == (1, 1, 1)


def test_before_layout_everything_is_one_row() -> None:
    assert row_sizes([10, 10], 0) == (2,)


def test_no_keys_no_rows() -> None:
    assert row_sizes([], 80) == ()


def test_key_width_counts_padding() -> None:
    assert key_width(FooterKey("q", "q", "Quit", "quit")) == 1 + 2 + 4 + 1
    assert key_width(FooterKey("q", "q", "", "quit")) == 3


def rows(footer: WrappingFooter) -> list[list[str]]:
    return [
        [key.description for key in row.children if isinstance(key, FooterKey)]
        for row in footer.query(".footer-row")
    ]


async def test_the_deck_footer_wraps_and_unwraps_with_the_width() -> None:
    app = DeckApp(StubSource(SNAP), interval=3600)
    async with app.run_test(size=(200, 40)) as pilot:
        await settle(app, pilot)
        footer = app.screen.query_one(WrappingFooter)
        assert len(rows(footer)) == 1
        await pilot.resize_terminal(160, 40)
        for _ in range(3):
            await pilot.pause()
        shown = rows(footer)
        assert len(shown) == 2
        assert footer.size.height == 2
        assert "Quit" in shown[1]
        await pilot.resize_terminal(200, 40)
        for _ in range(3):
            await pilot.pause()
        assert len(rows(footer)) == 1
