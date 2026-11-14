"""Render the landing page's carousel screens from invented data (#106).

The real deck runs headless (Textual's `run_test`) on made-up projects, sessions, issues and
costs, is driven to each screen by key presses, and saves Textual's SVG screenshot; headless
Chrome turns each into a WebP at 1.25×, since the SVG fetches its font from a CDN and the
page loads nothing from elsewhere. The phone slide is the real phone page, served by the real
daemon code from the same invented snapshot. Nothing here reads the machine: the paths are
under /home/demo, and `main` points every XDG directory at a scratch one first.

Re-run after a change to the deck: `uv run scripts/make_screens.py` (needs google-chrome or
chromium on PATH, and the background extra for Pillow). Writes site/assets/screens/*.webp.
"""

import asyncio
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cardak.core.agents import Status, WaitKind

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "site" / "assets" / "screens"
SIZE = (160, 40)
DETAIL_SIZE = (120, 24)  # the details of one wait: a few lines, bigger text
USAGE_SIZE = (160, 16)  # the cost table is short: a full-height screen would be mostly empty
HOME = Path("/home/demo")
CODE = HOME / "code"
GIB = 2**30
MIB = 2**20
REPO = "https://github.com/example"
ML_QUESTION = "Which split should the evaluation use: the 2025 holdout or a fresh random 10%?"

# (name, scheme, accent): the projects in projects.toml, each with its color scheme
PROJECTS = (
    ("webshop", "Catppuccin Mocha", "#f38ba8"),
    ("api-gateway", "Tokyo Night", "#7aa2f7"),
    ("ml-pipeline", "Gruvbox Dark", "#fabd2f"),
    ("docs-site", "Nord", "#88c0d0"),
    ("mobile-app", "Dracula", "#bd93f9"),
    ("infra", "Solarized Dark (Gogh)", "#2aa198"),
)


def _spark(values: list[float], top: float) -> str:
    from cardak.core.recent import spark

    return spark(values, top)


def _agent(
    sid: str,
    status: "Status",
    project: str,
    since: float,
    kind: "WaitKind | None" = None,
    reason: str | None = None,
    summary: str | None = None,
    context: int | None = None,
):
    from cardak.core.snapshot import AgentView

    return AgentView(
        sid,
        status,
        kind,
        since,
        reason,
        None,
        project,
        summary,
        context,
    )


def _pane(
    pane_id: int,
    project: str,
    sub: str,
    label: str,
    rss_mib: int,
    cpu: float,
    agent=None,
    shape: tuple[float, ...] = (0.4, 0.5, 0.5, 0.6, 0.6, 0.7, 0.8, 0.8),
):
    from cardak.core.snapshot import PaneRow

    cwd = CODE / project / sub if sub else CODE / project
    return PaneRow(
        pane_id=pane_id,
        label=label,
        detail=None,
        cwd=cwd,
        cwd_display=sub or ".",
        cpu_pct=cpu,
        rss=rss_mib * MIB,
        is_self=False,
        is_claude=agent is not None,
        agent=agent,
        mcp_rss=180 * MIB if agent is not None else 0,
        ram_spark=_spark([v * rss_mib for v in shape], 4096),
    )


def _panes(now: float, summaries: bool) -> dict[str, tuple]:
    """Each project's panes. Three sessions wait, with their reason and (maybe) a summary."""

    def say(text: str) -> str | None:
        return text if summaries else None

    return {
        "webshop": (
            _pane(
                3,
                "webshop",
                "",
                "claude",
                690,
                4.0,
                _agent(
                    "s-web",
                    "waiting",
                    "webshop",
                    now - 3900,
                    "permission",
                    "Bash: npm run test:e2e -- --project=checkout",
                    say("wants to run the checkout end-to-end tests"),
                    142_000,
                ),
            ),
            _pane(4, "webshop", "", "npm run dev", 840, 12.0, shape=(0.9,) * 8),
            _pane(5, "webshop", "", "zsh", 6, 0.0),
        ),
        "api-gateway": (
            _pane(
                7,
                "api-gateway",
                "",
                "claude",
                520,
                38.0,
                _agent("s-api", "working", "api-gateway", now - 95, context=88_000),
            ),
            _pane(
                8,
                "api-gateway",
                "",
                "cargo watch -x test",
                1460,
                96.0,
                shape=(0.3, 0.5, 0.9, 1, 0.6, 0.8, 1, 1),
            ),
        ),
        "ml-pipeline": (
            _pane(
                10,
                "ml-pipeline",
                "",
                "claude",
                610,
                2.0,
                _agent(
                    "s-ml",
                    "waiting",
                    "ml-pipeline",
                    now - 1460,
                    "question",
                    ML_QUESTION,
                    say("asks which split to evaluate on: the 2025 holdout or a new 10%"),
                    231_000,
                ),
            ),
            _pane(
                11,
                "ml-pipeline",
                "",
                "python train.py",
                5300,
                388.0,
                shape=(0.6, 0.7, 0.8, 0.9, 0.95, 1, 1, 1),
            ),
            _pane(12, "ml-pipeline", "notebooks", "jupyter lab", 910, 1.0),
        ),
        "docs-site": (
            _pane(
                14,
                "docs-site",
                "",
                "claude",
                430,
                0.0,
                _agent(
                    "s-docs",
                    "waiting",
                    "docs-site",
                    now - 260,
                    "finished",
                    "The migration guide is written and the build passes. Shall I open a PR?",
                    say("finished the migration guide; asks whether to open a PR"),
                    64_000,
                ),
            ),
            _pane(15, "docs-site", "", "hugo server", 180, 0.5),
        ),
        "mobile-app": (
            _pane(
                17,
                "mobile-app",
                "",
                "claude",
                560,
                21.0,
                _agent("s-mob", "working", "mobile-app", now - 40, context=171_000),
            ),
            _pane(
                18,
                "mobile-app",
                "",
                "gradle assembleDebug",
                2900,
                240.0,
                shape=(0.2, 0.4, 0.6, 0.8, 1, 1, 1, 1),
            ),
        ),
        "infra": (
            _pane(
                20,
                "infra",
                "",
                "claude",
                380,
                0.0,
                _agent("s-infra", "idle", "infra", now - 7200, context=45_000),
            ),
            _pane(21, "infra", "", "zsh", 7, 0.0),
        ),
    }


SPEND = {
    "webshop": 41.20,
    "api-gateway": 63.75,
    "ml-pipeline": 88.10,
    "docs-site": 12.40,
    "mobile-app": 35.60,
    "infra": 7.30,
}


def _project(name: str, scheme: str, accent: str, rows: tuple):
    from cardak.core.snapshot import DockerLine, Group

    rss = sum(r.rss + r.mcp_rss for r in rows)
    cpu = sum((r.cpu_pct or 0.0) + r.mcp_cpu_pct for r in rows)
    docker = ()
    if name == "webshop":
        docker = (DockerLine("docker: webshop-db-1", 410 * MIB, 1.5),)
        rss += 410 * MIB
    return Group(
        kind="config",
        key=CODE / name,
        name=name,
        accent=accent,
        path=f"~/code/{name}",
        cpu_pct=cpu,
        rss=rss,
        panes=rows,
        theme=scheme,
        docker=docker,
        ram_spark=_spark([rss * f for f in (0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1, 1)], 8 * GIB),
        spend_usd=SPEND[name],
    )


def demo_snapshot(now: float, summaries: bool = False):
    """A deck snapshot of six invented projects, three sessions waiting on you."""
    from cardak.collectors.machine import MachineSample
    from cardak.core.limits import LimitsView, LimitView
    from cardak.core.resources import ResourceSettings, derive
    from cardak.core.snapshot import WAITING_NAME, DeckSnapshot, Group, MachineView

    panes = _panes(now, summaries)
    groups = [_project(name, scheme, accent, panes[name]) for name, scheme, accent in PROJECTS]
    other_rows = (_pane(23, "", "", "htop", 9, 1.0),)
    other_rows = (replace(other_rows[0], cwd=HOME, cwd_display="~"),)
    other = Group(
        kind="other",
        key=None,
        name="Other",
        accent=None,
        path=None,
        cpu_pct=64.0,
        rss=6 * GIB,
        panes=other_rows,
        outside_rss=6 * GIB - 9 * MIB,
        outside_cpu_pct=63.0,
        ram_spark=_spark([5, 5.2, 5.5, 5.8, 6, 6, 6, 6], 32),
    )
    waiting_rows = sorted(
        (r for g in groups for r in g.panes if r.agent and r.agent.status == "waiting"),
        key=lambda r: r.agent.since if r.agent else 0.0,
    )
    waiting = Group(
        kind="waiting",
        key=None,
        name=WAITING_NAME,
        accent=None,
        path=None,
        cpu_pct=0.0,
        rss=0,
        panes=tuple(waiting_rows),
    )
    machine = MachineSample(64 * GIB, 41 * GIB, 8 * GIB, 8 * GIB, 34.0, 16)
    limits = LimitsView(
        LimitView(37, now + 2 * 3600 + 14 * 60, "ok"),
        LimitView(58, now + 3 * 86400 + 5 * 3600, "ok"),
        age_s=40,
    )
    all_groups = (waiting, *groups, other)
    return DeckSnapshot(
        groups=all_groups,
        n_panes=sum(len(g.panes) for g in all_groups if g.kind != "waiting"),
        n_waiting=len(waiting_rows),
        machine=machine,
        machine_view=MachineView(machine, None, "▃▃▄▄▄▅▅▅", "▂▃▅▃▄▆▄▃"),
        thresholds=derive(ResourceSettings(), machine.mem_total, machine.n_cpus),
        month_spend=sum(SPEND.values()) + 18.90,
        plan_limits=limits,
    )


def _day_cost(today: date, d: int, i: int, base: float) -> float:
    """Project i's cost d days into the window: a weekly rhythm, quieter weekends, each project
    busy in its own stretch, and some use today for every one."""
    from cardak.core.trends import DAYS

    if d == DAYS - 1:
        return round(base * (1.1 + i * 0.07), 2)
    weekday = (today.toordinal() - (DAYS - 1 - d)) % 7
    if (weekday in (5, 6) and (d + i) % 3) or (d * 7 + i * 5) % 13 == 0:
        return 0.0
    return round(base * (1 + 0.8 * ((d + 4 * i) % 11) / 10), 2)


def _trends(today: date):
    """30 days of invented use per project, cost by day."""
    from cardak.core.trends import DAYS, DayUsage, GroupedTrends, ProjectTrend

    rows = []
    every: list[float] = [0.0] * DAYS
    for i, (name, _, _) in enumerate(PROJECTS):
        days = []
        for d in range(DAYS):
            cost = _day_cost(today, d, i, SPEND[name] / 9)
            every[d] += cost
            days.append(
                DayUsage(
                    cost=cost,
                    tokens=int(cost * 310_000),
                    output_tokens=int(cost * 4_000),
                    replies=int(cost * 9),
                    prompts=int(cost * 1.2),
                    prompt_chars=int(cost * 260),
                    sessions=frozenset({f"{name}-{d}"}),
                )
                if cost
                else None
            )
        rows.append(ProjectTrend(f"project:{CODE / name}", name, tuple(days)))
    rows.append(
        ProjectTrend(
            "all",
            "All projects",
            tuple(DayUsage(cost=round(c, 2), replies=int(c * 9)) if c else None for c in every),
        )
    )
    return GroupedTrends(today, tuple(rows), ())


def _board(now: float):
    from cardak.collectors.github import Label
    from cardak.core.pm import Band, Board, Bullet, Card, Done

    def url(project: str, kind: str, n: int) -> str:
        return f"{REPO}/{project}/{kind}/{n}"

    bug, feat, ux = Label("bug", "d73a4a"), Label("enhancement", "a2eeef"), Label("ux", "fbca04")
    web = Band(
        "webshop",
        str(CODE / "webshop"),
        "example/webshop",
        (
            Card(
                "issue",
                231,
                "Coupon codes are lost on the payment step",
                (bug,),
                now - 5400,
                url("webshop", "issues", 231),
            ),
            Card(
                "issue",
                228,
                "Saved carts for signed-out visitors",
                (feat,),
                now - 86_400 * 2,
                url("webshop", "issues", 228),
            ),
            Card(
                "issue",
                219,
                "Product page: lazy-load the gallery",
                (ux,),
                now - 86_400 * 5,
                url("webshop", "issues", 219),
            ),
        ),
        (
            Card(
                "issue",
                225,
                "Checkout end-to-end tests",
                (),
                now - 600,
                url("webshop", "issues", 225),
                "225-checkout-e2e",
                None,
                "waiting",
            ),
            Card(
                "pr",
                233,
                "Bump the payment SDK",
                (),
                now - 3600,
                url("webshop", "pull", 233),
                "bump-payment-sdk",
                233,
                None,
            ),
        ),
        Done(
            (
                Bullet("Fixed tax rounding on mixed carts", (217,), url("webshop", "pull", 217)),
                Bullet(
                    "Order emails now show the delivery window",
                    (214, 215),
                    url("webshop", "pull", 215),
                ),
            ),
            None,
        ),
        now - 720,
        None,
    )
    api = Band(
        "api-gateway",
        str(CODE / "api-gateway"),
        "example/api-gateway",
        (
            Card(
                "issue",
                91,
                "Rate limits per API key",
                (feat,),
                now - 86_400 * 3,
                url("api-gateway", "issues", 91),
            ),
            Card(
                "issue",
                87,
                "Timeouts aren't logged with the route",
                (bug,),
                now - 86_400 * 6,
                url("api-gateway", "issues", 87),
            ),
        ),
        (
            Card(
                "issue",
                89,
                "Retry idempotent upstream calls",
                (),
                now - 300,
                url("api-gateway", "issues", 89),
                "89-retries",
                None,
                "working",
            ),
        ),
        Done(
            (Bullet("Health checks report each upstream", (84,), url("api-gateway", "pull", 84)),),
            None,
        ),
        now - 900,
        None,
    )
    docs = Band(
        "docs-site",
        str(CODE / "docs-site"),
        "example/docs-site",
        (
            Card(
                "issue",
                40,
                "Search results lose the version filter",
                (bug,),
                now - 86_400,
                url("docs-site", "issues", 40),
            ),
        ),
        (
            Card(
                "issue",
                38,
                "Migration guide for v3",
                (),
                now - 2000,
                url("docs-site", "issues", 38),
                "38-migration-guide",
                None,
                "waiting",
            ),
        ),
        Done((), "Nothing merged or closed in the last 7 days."),
        now - 1500,
        None,
    )
    return Board((web, api, docs), now - 600)


class _Pm:
    def __init__(self, board) -> None:
        self._board = board

    def board(self):
        return self._board

    def refresh(self, project=None) -> None:
        pass

    def observe(self, sessions) -> None:
        pass


class _Previewer:
    in_wezterm = True

    def preview(self, *args, **kwargs) -> None:
        pass

    def clear(self, *args, **kwargs) -> None:
        pass


SCHEMES = {
    # name: (background, foreground, the eight normal ANSI colors)
    "Catppuccin Mocha": (
        "#1e1e2e",
        "#cdd6f4",
        ("#45475a", "#f38ba8", "#a6e3a1", "#f9e2af", "#89b4fa", "#f5c2e7", "#94e2d5", "#bac2de"),
    ),
    "Dracula": (
        "#282a36",
        "#f8f8f2",
        ("#21222c", "#ff5555", "#50fa7b", "#f1fa8c", "#bd93f9", "#ff79c6", "#8be9fd", "#f8f8f2"),
    ),
    "Gruvbox Dark": (
        "#282828",
        "#ebdbb2",
        ("#282828", "#cc241d", "#98971a", "#d79921", "#458588", "#b16286", "#689d6a", "#a89984"),
    ),
    "Gruvbox Light": (
        "#fbf1c7",
        "#3c3836",
        ("#fbf1c7", "#cc241d", "#98971a", "#d79921", "#458588", "#b16286", "#689d6a", "#7c6f64"),
    ),
    "Kanagawa (Gogh)": (
        "#1f1f28",
        "#dcd7ba",
        ("#090618", "#c34043", "#76946a", "#c0a36e", "#7e9cd8", "#957fb8", "#6a9589", "#c8c093"),
    ),
    "Monokai Pro (Gogh)": (
        "#2d2a2e",
        "#fcfcfa",
        ("#403e41", "#ff6188", "#a9dc76", "#ffd866", "#fc9867", "#ab9df2", "#78dce8", "#fcfcfa"),
    ),
    "Nord": (
        "#2e3440",
        "#d8dee9",
        ("#3b4252", "#bf616a", "#a3be8c", "#ebcb8b", "#81a1c1", "#b48ead", "#88c0d0", "#e5e9f0"),
    ),
    "One Dark (Gogh)": (
        "#282c34",
        "#abb2bf",
        ("#282c34", "#e06c75", "#98c379", "#e5c07b", "#61afef", "#c678dd", "#56b6c2", "#abb2bf"),
    ),
    "Rosé Pine (Gogh)": (
        "#191724",
        "#e0def4",
        ("#26233a", "#eb6f92", "#31748f", "#f6c177", "#9ccfd8", "#c4a7e7", "#ebbcba", "#e0def4"),
    ),
    "Solarized Dark (Gogh)": (
        "#002b36",
        "#839496",
        ("#073642", "#dc322f", "#859900", "#b58900", "#268bd2", "#d33682", "#2aa198", "#eee8d5"),
    ),
    "Solarized Light (Gogh)": (
        "#fdf6e3",
        "#657b83",
        ("#073642", "#dc322f", "#859900", "#b58900", "#268bd2", "#d33682", "#2aa198", "#eee8d5"),
    ),
    "Ayu Mirage": (
        "#1f2430",
        "#cbccc6",
        ("#191e2a", "#ed8274", "#a6cc70", "#fad07b", "#6dcbfa", "#cfbafa", "#90e1c6", "#c7c7c7"),
    ),
    "Everforest Dark (Gogh)": (
        "#2d353b",
        "#d3c6aa",
        ("#475258", "#e67e80", "#a7c080", "#dbbc7f", "#7fbbb3", "#d699b6", "#83c092", "#d3c6aa"),
    ),
    "GitHub Dark": (
        "#101216",
        "#8b949e",
        ("#000000", "#f78166", "#56d364", "#e3b341", "#6ca4f8", "#db61a2", "#2b7489", "#ffffff"),
    ),
    "Material Darker (base16)": (
        "#212121",
        "#eeffff",
        ("#212121", "#f07178", "#c3e88d", "#ffcb6b", "#82aaff", "#c792ea", "#89ddff", "#eeffff"),
    ),
    "Tokyo Night": (
        "#1a1b26",
        "#c0caf5",
        ("#15161e", "#f7768e", "#9ece6a", "#e0af68", "#7aa2f7", "#bb9af7", "#7dcfff", "#a9b1d6"),
    ),
}


def _schemes():
    from cardak.core.themes import Scheme

    return {
        name: Scheme(name, bg, fg, None, ansi + ansi) for name, (bg, fg, ansi) in SCHEMES.items()
    }


class _Source:
    """The deck's source, answering from the invented snapshot."""

    def __init__(self, snap, sessions) -> None:
        self.snap = snap
        self.sessions = sessions
        self.can_add = True

    def poll(self):
        return self.snap

    def add_project(self, name, path) -> None:
        pass

    def set_look(self, project_path, theme, background, name=None) -> None:
        pass

    def background_on(self, project_path) -> bool:
        return True

    def issue_command(self) -> str:
        return "/issue-to-pr {number}"

    def regenerate(self) -> None:
        pass

    def mark_seen_pane(self, pane_id) -> None:
        pass

    def session(self, session_id):
        return self.sessions.get(session_id)

    def summary_info(self, state):
        from cardak.core.summary import SummaryInfo

        return SummaryInfo("wants to run the checkout end-to-end tests", 1.84, 5.0)


def _sessions(now: float) -> dict:
    from cardak.core.agents import SessionState

    web = SessionState(
        "s-web",
        pane_id=3,
        cwd=CODE / "webshop",
        status="waiting",
        kind="permission",
        status_since=now - 3900,
        reason="Bash: npm run test:e2e -- --project=checkout",
        last_prompt="Add end-to-end tests for the checkout, coupon codes included (#225)",
        last_prompt_at=now - 5100,
        last_message="The unit tests pass. Now the end-to-end suite for the checkout flow.",
        current_tool="Bash",
        turn_edits=6,
        turn_reads=14,
        turn_commands=5,
        turn_files=("tests/e2e/checkout.spec.ts", "src/checkout/coupon.ts", "playwright.config.ts"),
        permission_requested=True,
        started_at=now - 9000,
        updated_at=now - 3900,
    )
    ml = SessionState(
        "s-ml",
        pane_id=10,
        cwd=CODE / "ml-pipeline",
        status="waiting",
        kind="question",
        status_since=now - 1460,
        reason=ML_QUESTION,
        last_prompt="Evaluate the new model against last quarter's",
        last_prompt_at=now - 2400,
        current_tool="AskUserQuestion",
        started_at=now - 7000,
        updated_at=now - 1460,
    )
    docs = SessionState(
        "s-docs",
        pane_id=14,
        cwd=CODE / "docs-site",
        status="waiting",
        kind="finished",
        status_since=now - 260,
        reason="The migration guide is written and the build passes. Shall I open a PR?",
        last_prompt="Write the migration guide for v3 (#38)",
        last_prompt_at=now - 1800,
        last_message="The migration guide is written and the build passes. Shall I open a PR?",
        turn_edits=4,
        turn_reads=11,
        turn_commands=2,
        started_at=now - 4000,
        updated_at=now - 260,
    )
    return {"s-web": web, "s-ml": ml, "s-docs": docs}


async def _shoot(app, keys: tuple[str, ...], out: Path, size: tuple[int, int], before=None) -> None:
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        await pilot.pause(0.3)
        if before is not None:
            before(app)
        for key in keys:
            await pilot.press(key)
            await pilot.pause(0.2)
        await pilot.pause(0.5)
        out.write_text(app.export_screenshot())


def _cursor_to(app, project: str) -> None:
    from cardak.ui.app import node_id

    tree = app.deck_tree
    for node in tree.root.children:
        if node.data is not None and node.data.group.name == project:
            tree.move_cursor(node)
            return
    raise LookupError(
        f"{project} isn't in the deck ({[node_id(n.data) for n in tree.root.children]})"
    )


class _Log:
    """The resource history: 30 days of invented peaks per project."""

    def __init__(self, today: date) -> None:
        from cardak.core.store import DayPeak
        from cardak.core.trends import DAYS

        self.days = []
        for i, (name, _, _) in enumerate(PROJECTS):
            for d in range(DAYS):
                day = date.fromordinal(today.toordinal() - (DAYS - 1 - d)).isoformat()
                ram = int((0.8 + i * 0.5 + ((d * 3 + i) % 7) * 0.25) * GIB)
                self.days.append(DayPeak(f"project:{CODE / name}", name, day, ram, 40.0 + 30 * i))

    def resource_events_since(self, ts):
        return []

    def latest_crash(self, since):
        return None

    def dismiss_report(self, ts) -> None:
        pass

    def history_days(self, since):
        return self.days

    def history_medians(self, since):
        return {}

    def history_hours(self, subject, since):
        return []

    def alert_counts(self, since):
        return {}


def _app(snap, now: float, **kw):
    from cardak.core.themes import SchemeStore
    from cardak.ui.app import DeckApp

    schemes = _schemes()
    return DeckApp(
        _Source(snap, _sessions(now)),
        interval=3600,
        log=_Log(date.fromtimestamp(now)),
        pm=_Pm(_board(now)),
        schemes=SchemeStore(schemes),
        load_schemes=lambda: schemes,
        previewer=_Previewer(),  # type: ignore[arg-type]
        hooks_status=lambda: None,
        **kw,
    )


async def _deck_svgs(out: Path, size: tuple[int, int]) -> dict[str, Path]:
    now = time.time()
    plain = demo_snapshot(now)
    said = demo_snapshot(now, summaries=True)
    with_trends = replace(plain, usage_trends=_trends(date.fromtimestamp(now)))
    # (slide, snapshot, keys, what to do before the keys)
    slides = (
        ("overview", plain, (), lambda app: _cursor_to(app, "webshop")),
        ("waiting", plain, ("tab", "i"), None, DETAIL_SIZE),
        ("summaries", said, ("w", "tab"), None),
        ("usage", with_trends, ("h", "c", "c"), None, USAGE_SIZE),
        ("board", plain, ("P",), None),
        ("colors", plain, ("t",), lambda app: _cursor_to(app, "webshop")),
    )
    made = {}
    for name, snap, keys, before, *own in slides:
        path = out / f"{name}.svg"
        await _shoot(_app(snap, now), keys, path, own[0] if own else size, before)
        made[name] = path
    return made


def deck_svgs(out: Path, size: tuple[int, int] = SIZE) -> dict[str, Path]:
    """The six deck slides as Textual SVG screenshots, by slide name."""
    out.mkdir(parents=True, exist_ok=True)
    return asyncio.run(_deck_svgs(out, size))


def _chrome() -> str:
    chrome = shutil.which("google-chrome") or shutil.which("chromium")
    if chrome is None:
        sys.exit("make_screens: needs google-chrome or chromium on PATH")
    return chrome


SCALE = 1.25  # ~2460 px wide: sharp in the page's ~1100 px slot on a 2× screen, and small


def _webp(png: Path, out: Path) -> None:
    from PIL import Image  # the background extra; `uv sync --all-extras`

    with Image.open(png) as im:
        im.save(out, "WEBP", quality=88, method=6)


def _screenshot(
    chrome: str, url: str, png: Path, width: int, height: int, scale: float = SCALE
) -> None:
    subprocess.run(
        [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--allow-file-access-from-files",
            f"--force-device-scale-factor={scale}",
            f"--window-size={width},{height}",
            "--virtual-time-budget=4000",
            f"--screenshot={png}",
            url,
        ],
        check=True,
        capture_output=True,
        timeout=90,
    )


def to_webp(chrome: str, svg: Path, out: Path) -> None:
    """Chrome draws the SVG at SCALE, cropped to its own size; saved as WebP."""
    found = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg.read_text())
    if found is None:
        raise ValueError(f"{svg} has no viewBox")
    width, height = (round(float(v)) for v in found.groups())
    page = svg.with_suffix(".html")
    page.write_text(
        '<body style="margin:0;background:#0c0c0c">'
        f'<img src="{svg.as_uri()}" width="{width}" height="{height}" style="display:block">'
    )
    png = svg.with_suffix(".png")
    _screenshot(chrome, page.as_uri(), png, width, height)
    _webp(png, out)


PHONE = (390, 844)
KEY = "demo-key-not-a-secret-0123456789abcdefghijk"


def phone_webp(chrome: str, out: Path, scratch: Path) -> tuple[Path, Path]:
    """The phone page, served by the daemon's own code from the invented snapshot: a portrait
    shot, and the same shot on a landscape slide the size of the deck's."""
    from cardak.web.server import Latest, make_server, start

    now = time.time()
    latest = Latest()
    latest.publish(demo_snapshot(now, summaries=True), now)
    sessions = _sessions(now)
    server = make_server("127.0.0.1", 0, KEY, latest, sessions.get, board=lambda: _board(now))
    start(server, poll_interval=0.05)
    try:
        port = server.server_address[1]
        portrait = scratch / "phone-portrait.png"
        _screenshot(chrome, f"http://127.0.0.1:{port}/#k={KEY}", portrait, *PHONE, scale=2)
    finally:
        server.shutdown()
        server.server_close()
    frame = scratch / "phone-frame.html"
    frame.write_text(
        '<body style="margin:0;width:1280px;height:800px;background:#0f2a44;display:flex;'
        'align-items:center;justify-content:center">'
        '<div style="padding:12px;background:#05101b;border-radius:44px;'
        'box-shadow:0 20px 60px rgba(0,0,0,.45)">'
        f'<img src="{portrait.as_uri()}" style="display:block;height:740px;border-radius:32px">'
        "</div>"
    )
    landscape = scratch / "phone.png"
    _screenshot(chrome, frame.as_uri(), landscape, 1280, 800, scale=2)
    made = out / "phone-portrait.webp", out / "phone.webp"
    _webp(portrait, made[0])
    _webp(landscape, made[1])
    return made


def _scratch_env(scratch: Path) -> None:
    """Point everything cardak reads or writes at the scratch dir, before it runs."""
    for name in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME"):
        (scratch / name).mkdir()
        os.environ[name] = str(scratch / name)
    os.environ["CARDAK_CONFIG"] = str(scratch / "projects.toml")
    os.environ.pop("WEZTERM_PANE", None)
    os.environ.pop("WEZTERM_UNIX_SOCKET", None)


def main() -> None:
    chrome = _chrome()
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp)
        _scratch_env(scratch)
        for name, svg in deck_svgs(scratch / "svg").items():
            to_webp(chrome, svg, OUT / f"{name}.webp")
            print(f"wrote {name}.webp")
        phone_webp(chrome, OUT, scratch)
        print("wrote phone.webp, phone-portrait.webp")


if __name__ == "__main__":
    main()
