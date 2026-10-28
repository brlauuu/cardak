import time
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
from cardak.core.trends import DAYS, LABELS, DayUsage, GroupedTrends, ProjectTrend
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


async def test_axis_lines_up_with_day_columns() -> None:
    app = DeckApp(StubSource(live()), interval=3600, log=log())
    async with app.run_test(size=(100, 16)) as pilot:
        await settle(app, pilot)
        await pilot.press("h")
        await pilot.pause()
        lines = [s.text for s in app.screen._compositor.render_strips()]
        axis = next(x for x in lines if f"{date.today():%b %-d}" in x and "history" not in x)
        cells = next(x for x in lines if "█" in x and x.startswith("webshop"))
        assert axis.rstrip().endswith(f"{date.today():%b %-d}")
        assert len(axis.rstrip()) - 1 == cells.rindex("█")  # today's label ends over today's cell


async def test_plot_survives_markup_in_names_and_events() -> None:
    odd = FakeLog(
        events=[ResourceEvent(time.time(), "big", "warning", WEB, "web [/x] · 9 GB", True)],
        days=[DayPeak(WEB, "web[/x]", day(0), 9 * GIB, 300.0)],
    )
    app = DeckApp(StubSource(live()), interval=3600, log=odd)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        await pilot.press("h")
        await pilot.pause()
        table = app.screen.query_one(DataTable)
        names = [str(table.get_row_at(i)[0]).strip() for i in range(table.row_count)]
        table.move_cursor(row=names.index("web[/x]"), column=len(table.columns) - 1)
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, PlotScreen)
        assert "web [/x]" in str(app.screen.query_one("#day", Static).render())


def _days(**cells: DayUsage) -> tuple[DayUsage | None, ...]:
    """Cells by "dN" = N days ago."""
    out: list[DayUsage | None] = [None] * DAYS
    for k, d in cells.items():
        out[DAYS - 1 - int(k[1:])] = d
    return tuple(out)


def usage_live(trends: GroupedTrends | None | bool = True) -> DeckSnapshot:
    if trends is True:
        today = DayUsage(cost=4.0, unpriced=1, replies=3, tokens=9000, output_tokens=300,
                         prompts=2, prompt_chars=80, sessions=frozenset({"s1"}))  # fmt: skip
        earlier = DayUsage(cost=8.0, replies=2, tokens=5000, output_tokens=100, prompts=1,
                           prompt_chars=10, sessions=frozenset({"s2"}))  # fmt: skip
        other = DayUsage(cost=1.0, replies=1, tokens=100, output_tokens=10,
                         sessions=frozenset({"s3"}))  # fmt: skip
        trends = GroupedTrends(
            date.today(),
            (
                ProjectTrend(WEB, "webshop", _days(d0=today, d3=earlier)),
                ProjectTrend("other", "Other", _days(d0=other)),
                ProjectTrend("all", "All projects", _days(d0=DayUsage(cost=40.0), d3=earlier)),
            ),
            ("claude-x",),
        )
    return replace(live(), usage_trends=trends or None)


def headers(table: DataTable) -> list[str]:
    return [str(c.label).strip() for c in table.columns.values()][:5]


def names(table: DataTable) -> list[str]:
    return [str(table.get_row_at(i)[0]).strip() for i in range(table.row_count)]


async def test_cycle_through_every_figure_and_back() -> None:
    app = DeckApp(StubSource(usage_live()), interval=3600, log=log())
    async with app.run_test(size=(120, 30)) as pilot:
        await settle(app, pilot)
        await pilot.press("h", "c", "c")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, HistoryScreen)
        assert "cost" in title(screen) and "[c] next: tokens" in title(screen)
        table = screen.query_one(DataTable)
        assert headers(table) == ["project", "today", "per day", "peak", "30 d"]
        for _ in range(7):
            await pilot.press("c")
        await pilot.pause()
        assert "RAM" in title(screen).split("[")[0]
        assert headers(table) == ["project", "now", "median", "peak", "alerts"]


async def test_usage_rows_and_marks() -> None:
    app = DeckApp(StubSource(usage_live()), interval=3600, log=log())
    async with app.run_test(size=(120, 30)) as pilot:
        await settle(app, pilot)
        await pilot.press("h", "c", "c")
        await pilot.pause()
        table = app.screen.query_one(DataTable)
        assert names(table) == ["webshop", "Other", "All projects"]
        row = [str(x).strip() for x in table.get_row_at(0)[:5]]
        assert row == ["webshop", "$4.00+", "$6.00", "$8.00", "$12+"]
        await pilot.press("s", "s", "s", "s")  # name
        assert names(table)[-2:] == ["Other", "All projects"]
        legend = str(app.screen.query_one("#legend", Static).render())
        assert "relative to the busiest day" in legend and "no price: claude-x" in legend


async def test_usage_cell_shows_the_day() -> None:
    app = DeckApp(StubSource(usage_live()), interval=3600, log=log())
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        await pilot.press("h", "c", "c")
        await pilot.pause()
        table = app.screen.query_one(DataTable)
        table.move_cursor(row=0, column=len(table.columns) - 1)
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, HistoryScreen)
        text = [n.message for n in app._notifications][-1]
        assert f"{date.today():%a %b %-d}" in text and "webshop" in text
        assert all(label in text for label in LABELS.values())
        table.move_cursor(row=0, column=len(table.columns) - 2)
        await pilot.press("enter")
        await pilot.pause()
        assert "no use" in [n.message for n in app._notifications][-1]


async def test_usage_without_trends_shows_hint() -> None:
    app = DeckApp(StubSource(usage_live(None)), interval=3600, log=log())
    async with app.run_test(size=(120, 30)) as pilot:
        await settle(app, pilot)
        await pilot.press("h", "c", "c")
        await pilot.pause()
        hint = str(app.screen.query_one("#hint", Static).render())
        assert hint == "No usage yet: cardak counts it while the deck or the daemon runs."
        assert app.screen.query_one(DataTable).row_count == 0


async def test_all_projects_scaled_on_its_own() -> None:
    app = DeckApp(StubSource(usage_live()), interval=3600, log=log())
    async with app.run_test(size=(120, 30)) as pilot:
        await settle(app, pilot)
        await pilot.press("h", "c", "c")
        await pilot.pause()
        table = app.screen.query_one(DataTable)
        webshop, everything = table.get_row_at(0), table.get_row_at(2)
        assert str(webshop[-4]) == "█"  # $8, three days ago: the busiest project cell
        assert str(everything[-1]) == "█"  # $40 today: All projects' own busiest
        assert str(everything[-4]) != "█"
