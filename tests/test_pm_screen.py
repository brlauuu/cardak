from pathlib import Path

from textual.app import App
from textual.widgets import OptionList, Static

from cardak.collectors.github import Label
from cardak.core.pm import NOTHING_DONE, Band, Board, Bullet, Card, Done
from cardak.ui.pm import Column, PmScreen

NOW = 2_000_000_000.0
U = "https://github.com/example/webshop"
WEBSHOP = Path("/w/webshop")
API = Path("/w/api-gw")


def webshop(error: str | None = None) -> Band:
    return Band(
        "webshop",
        str(WEBSHOP),
        "example/webshop",
        (
            Card(
                "issue",
                212,
                "Checkout loses the coupon",
                (Label("bug", "d73a4a"),),
                NOW - 7200,
                f"{U}/issues/212",
            ),
            Card(
                "issue",
                198,
                "Wishlist sharing",
                (Label("odd", "nothex"),),
                NOW - 90_000,
                f"{U}/issues/198",
            ),
        ),
        (
            Card(
                "issue",
                205,
                "Search filters",
                (),
                NOW - 60,
                f"{U}/issues/205",
                "205-search-filters",
                None,
                "working",
            ),
            Card("pr", 210, "Fix a typo", (), NOW - 30, f"{U}/pull/210", "fix-typo", 210, None),
        ),
        Done((Bullet("Fixed tax rounding", (203,), f"{U}/pull/203"),), None),
        NOW - 720,
        error,
    )


def api() -> Band:
    return Band(
        "api-gw",
        str(API),
        "example/api-gw",
        (
            Card(
                "issue",
                88,
                "Rate limit per key",
                (),
                NOW - 3 * 86_400,
                "https://github.com/example/api-gw/issues/88",
            ),
        ),
        (),
        Done((), NOTHING_DONE),
        NOW - 60,
        None,
    )


class FakePm:
    def __init__(self, board: Board | None) -> None:
        self.current = board
        self.refreshed: list[Path | None] = []

    def board(self) -> Board | None:
        return self.current

    def refresh(self, project: Path | None = None) -> None:
        self.refreshed.append(project)

    def observe(self, sessions: object) -> None:
        pass


class Host(App[None]):
    def __init__(self, screen: PmScreen) -> None:
        super().__init__()
        self._pm = screen

    def on_mount(self) -> None:
        self.push_screen(self._pm)


def screen_for(board: Board | None, project: Path | None = None, opened: list[str] | None = None):  # noqa: ANN201
    pm = FakePm(board)
    sink = opened if opened is not None else []
    return pm, PmScreen(pm, project, open_url=sink.append, clock=lambda: NOW)


def text_of(screen: PmScreen) -> str:
    parts = [str(s.render()) for s in screen.query(Static)]
    for column in screen.query(Column):
        parts += [str(column.get_option_at_index(i).prompt) for i in range(column.option_count)]
    return "\n".join(parts)


BOARD = Board((webshop(), api()), NOW)


async def until(pilot, check, limit: float = 10.0) -> None:  # noqa: ANN001
    """Wait for the screen's 1 s redraw, however slow the machine is."""
    for _ in range(int(limit / 0.1)):
        if check():
            return
        await pilot.pause(0.1)
    assert check()


async def test_all_projects_shows_a_band_per_project() -> None:
    _, screen = screen_for(BOARD)
    async with Host(screen).run_test() as pilot:
        await pilot.pause()
        text = text_of(screen)
        assert "webshop · 3 open · fetched 12 minutes ago" in text
        assert "api-gw · 1 open" in text
        assert "todo (2)" in text and "onit (2)" in text and "done · 7 days" in text
        assert len(screen.query(Column)) == 6


async def test_card_shows_number_title_labels_and_age() -> None:
    _, screen = screen_for(BOARD)
    async with Host(screen).run_test() as pilot:
        await pilot.pause()
        text = text_of(screen)
        assert "#212 Checkout loses the coupon" in text and " bug " in text
        assert "updated 2 hours ago" in text and " odd " in text  # a bad color still shows


async def test_onit_card_shows_branch_session_and_pr() -> None:
    _, screen = screen_for(BOARD)
    async with Host(screen).run_test() as pilot:
        await pilot.pause()
        text = text_of(screen)
        assert "205-search-filters" in text and "working" in text
        assert "PR #210 open" in text
        assert "Fixed tax rounding #203" in text


async def test_enter_opens_url() -> None:
    opened: list[str] = []
    _, screen = screen_for(BOARD, opened=opened)
    async with Host(screen).run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert opened == [f"{U}/issues/212"]


async def test_arrows_move_between_columns_and_bands() -> None:
    opened: list[str] = []
    _, screen = screen_for(BOARD, opened=opened)
    async with Host(screen).run_test() as pilot:
        await pilot.pause()
        await pilot.press("down", "down")  # past webshop's last todo card → api-gw's todo
        await pilot.press("enter")
        await pilot.press("up")  # back to webshop's last todo card
        await pilot.press("enter")
        await pilot.press("right", "enter")  # webshop's onit
        await pilot.press("right", "enter")  # webshop's done bullet
        await pilot.pause()
        assert opened == [
            "https://github.com/example/api-gw/issues/88",
            f"{U}/issues/198",
            f"{U}/issues/205",
            f"{U}/pull/203",
        ]


async def test_r_refreshes_this_project_only() -> None:
    pm, screen = screen_for(Board((webshop(),), NOW), project=WEBSHOP)
    async with Host(screen).run_test() as pilot:
        await pilot.pause()
        await pilot.press("R")
        assert pm.refreshed == [WEBSHOP]


async def test_r_on_all_projects_refreshes_all() -> None:
    pm, screen = screen_for(BOARD)
    async with Host(screen).run_test() as pilot:
        await pilot.pause()
        await pilot.press("R")
        assert pm.refreshed == [None]


async def test_one_project_shows_only_it() -> None:
    _, screen = screen_for(BOARD, project=API)
    async with Host(screen).run_test() as pilot:
        await pilot.pause()
        assert "webshop" not in text_of(screen) and len(screen.query(Column)) == 3


async def test_band_error_and_nothing_done() -> None:
    _, screen = screen_for(Board((webshop("can't reach GitHub"), api()), NOW))
    async with Host(screen).run_test() as pilot:
        await pilot.pause()
        text = text_of(screen)
        assert "can't reach GitHub" in text and NOTHING_DONE in text


async def test_no_board_yet() -> None:
    pm, screen = screen_for(None)
    async with Host(screen).run_test() as pilot:
        await pilot.pause()
        assert "Reading GitHub…" in text_of(screen)
        pm.current = BOARD
        await until(pilot, lambda: "webshop · 3 open" in text_of(screen))


async def test_redraw_keeps_the_highlighted_card() -> None:
    pm, screen = screen_for(BOARD)
    async with Host(screen).run_test() as pilot:
        await pilot.pause()
        await pilot.press("down")
        moved = webshop()
        newer = Card("issue", 300, "New one", (), NOW, f"{U}/issues/300")
        pm.current = Board(
            (Band(**{**moved.__dict__, "todo": (newer, *moved.todo)}), api()), NOW + 1
        )
        await until(pilot, lambda: "#300 New one" in text_of(screen))
        focused = screen.focused
        assert isinstance(focused, OptionList)
        assert focused.highlighted == 2  # still #198, now third


async def test_escape_closes() -> None:
    _, screen = screen_for(BOARD)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, PmScreen)
