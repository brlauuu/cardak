from dataclasses import replace
from datetime import date, datetime, timedelta

from logfix import FakeLog
from test_app import SNAP, StubSource, settle
from textual.widgets import DataTable, Static

from cardak.collectors.machine import MachineSample
from cardak.core.config import ResourceSettings
from cardak.core.resources import derive
from cardak.core.snapshot import DeckSnapshot
from cardak.core.store import DayPeak, ResourceEvent
from cardak.ui.app import DeckApp
from cardak.ui.history import HistoryScreen, PlotScreen

GIB = 2**30
WEB = "project:/home/user/repos/webshop"
API = "project:/home/user/repos/api-gw"
MACHINE = MachineSample(32 * GIB, 16 * GIB, GIB, GIB, 20.0, 16)


def day(offset: int) -> str:
    return (date.today() - timedelta(days=offset)).isoformat()


def log() -> FakeLog:
    today_noon = datetime.combine(date.today(), datetime.min.time()).timestamp() + 12 * 3600
    return FakeLog(
        events=[ResourceEvent(today_noon, "big", "warning", WEB, "webshop · pytest · 9 GB", True)],
        days=[
            DayPeak(WEB, "webshop", day(0), 9 * GIB, 300.0),
            DayPeak(WEB, "webshop", day(3), 3 * GIB, 100.0),
            DayPeak(API, "api-gw", day(1), 1 * GIB, 50.0),
            DayPeak("other", "Other", day(0), 12 * GIB, 40.0),
        ],
        medians={WEB: (2.0 * GIB, 50.0), API: (GIB, 20.0), "other": (8.0 * GIB, 30.0)},
        hours={WEB: [(int(today_noon) // 3600 * 3600, 9 * GIB, 300.0)]},
    )


def live() -> DeckSnapshot:
    t = derive(ResourceSettings(), MACHINE.mem_total, MACHINE.n_cpus)
    return replace(SNAP, machine=MACHINE, thresholds=t)


def title(screen: HistoryScreen) -> str:
    return str(screen.query_one("#title", Static).render())


async def test_history_screen_rows_and_keys() -> None:
    app = DeckApp(StubSource(live()), interval=3600, log=log())
    async with app.run_test(size=(120, 30)) as pilot:
        await settle(app, pilot)
        await pilot.press("h")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, HistoryScreen)
        table = screen.query_one(DataTable)
        names = [str(table.get_row_at(i)[0]).strip() for i in range(table.row_count)]
        assert names[-1] == "Other" and set(names) >= {"webshop", "api-gw", "Other"}
        assert names.index("webshop") < names.index("api-gw")  # peak 9 GB before 1 GB
        assert "RAM" in title(screen) and "sort: peak" in title(screen)
        assert str(table.get_row_at(names.index("webshop"))[-1]) == "█"
        await pilot.press("s")
        assert "sort: now" in title(screen)
        await pilot.press("c")
        assert "CPU" in title(screen).split("[")[0]
        legend = str(screen.query_one("#legend", Static).render())
        assert legend.startswith("peak CPU per day:")
        await pilot.press("escape")
        assert not isinstance(app.screen, HistoryScreen)


async def test_history_screen_empty_hint() -> None:
    app = DeckApp(StubSource(SNAP), interval=3600, log=FakeLog())
    async with app.run_test(size=(120, 30)) as pilot:
        await settle(app, pilot)
        await pilot.press("h")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, HistoryScreen)
        hint = str(screen.query_one("#hint", Static).render())
        assert hint == "No history yet: the daemon records it (cardak resources install)."
        assert screen.query_one(DataTable).row_count >= 1  # live projects, empty cells


async def test_history_without_log_notifies() -> None:
    app = DeckApp(StubSource(SNAP), interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("h")
        await pilot.pause()
        assert [n.message for n in app._notifications] == ["History unavailable."]


async def test_day_cell_opens_plot_and_arrows_move() -> None:
    app = DeckApp(StubSource(live()), interval=3600, log=log())
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        await pilot.press("h")
        await pilot.pause()
        table = app.screen.query_one(DataTable)
        names = [str(table.get_row_at(i)[0]).strip() for i in range(table.row_count)]
        table.move_cursor(row=names.index("webshop"), column=len(table.columns) - 1)
        await pilot.press("enter")
        await pilot.pause()
        plot = app.screen
        assert isinstance(plot, PlotScreen)
        info = str(plot.query_one("#day", Static).render())
        assert info.startswith(f"{date.today():%b %-d}: peak 9.0 GB · 1 alert")
        assert "webshop · pytest · 9 GB" in info
        await pilot.press("left")
        info = str(plot.query_one("#day", Static).render())
        assert info.startswith(f"{date.today() - timedelta(days=1):%b %-d}: no data")
        await pilot.press("escape")
        assert isinstance(app.screen, HistoryScreen)
