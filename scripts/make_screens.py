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
EVENTS_SIZE = (140, 20)  # the event log: a dozen lines
RECAP_SIZE = (120, 28)  # one note and the commits since
CONTEXT_SIZE = (120, 26)  # the plot takes ~18 rows: bigger text in the slide
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


def _docker(name: str, more: bool) -> tuple:
    """A project's Docker lines: webshop's database, and with `more` its cache and a build."""
    from cardak.core.snapshot import DockerLine

    if name == "webshop":
        lines = [DockerLine("docker: webshop-db-1", 410 * MIB, 1.5)]
        if more:
            lines.append(DockerLine("docker: webshop-redis-1", 38 * MIB, 0.4))
        return tuple(lines)
    if name == "api-gateway" and more:
        return (DockerLine("docker build", 2300 * MIB, 180.0, kind="build"),)
    return ()


def _totals(rows: tuple, docker: tuple) -> tuple[int, float]:
    """A project's RAM and CPU: its panes, their MCP servers, and its Docker lines."""
    rss = sum(r.rss + r.mcp_rss for r in rows) + sum(d.rss for d in docker)
    cpu = sum((r.cpu_pct or 0.0) + r.mcp_cpu_pct for r in rows)
    return rss, cpu + sum(d.cpu_pct or 0.0 for d in docker)


def _project(name: str, scheme: str, accent: str, rows: tuple, more_docker: bool = False):
    from cardak.core.snapshot import Group

    if name == "api-gateway" and more_docker:  # the pane running the build's client
        rows = (*rows, _pane(9, name, "", "docker compose build", 40, 3.0))
    docker = _docker(name, more_docker)
    rss, cpu = _totals(rows, docker)
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


def demo_snapshot(now: float, summaries: bool = False, docker: bool = False):
    """A deck snapshot of six invented projects, three sessions waiting on you; `docker` adds
    a cache container and a running build."""
    from cardak.collectors.machine import MachineSample
    from cardak.core.limits import LimitsView, LimitView
    from cardak.core.resources import ResourceSettings, derive
    from cardak.core.snapshot import WAITING_NAME, DeckSnapshot, Group, MachineView

    panes = _panes(now, summaries)
    groups = [
        _project(name, scheme, accent, panes[name], docker) for name, scheme, accent in PROJECTS
    ]
    htop, blog = _pane(23, "", "", "htop", 9, 1.0), _pane(24, "", "", "nvim", 60, 0.5)
    other_rows = (
        replace(htop, cwd=HOME, cwd_display="~"),
        replace(blog, cwd=CODE / "blog", cwd_display="~/code/blog"),  # not a project yet: `a`
    )
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
        return _events(time.time())

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


def _events(now: float) -> list:
    """The event log: what the daemon noticed over the last two days."""
    from cardak.core.store import ResourceEvent

    def project(name: str) -> str:
        return f"project:{CODE / name}"

    return [
        ResourceEvent(now - 30 * 3600, "daemon", "info", "daemon", "cardak daemon started.", False),
        ResourceEvent(
            now - 26 * 3600,
            "busy",
            "warning",
            project("api-gateway"),
            "api-gateway at 390% CPU for 2 min (cargo watch -x test).",
            True,
        ),
        ResourceEvent(
            now - 9 * 3600,
            "big",
            "warning",
            project("mobile-app"),
            "mobile-app uses 17.8 GB (gradle assembleDebug 15.2 GB).",
            True,
        ),
        ResourceEvent(
            now - 3 * 3600,
            "growth",
            "warning",
            project("ml-pipeline"),
            "ml-pipeline grew 2.1 GB in 5 min (python train.py).",
            True,
        ),
        ResourceEvent(
            now - 3 * 3600 + 40,
            "memory",
            "critical",
            "machine",
            "Memory critical: 3.1 GB available of 64 GB. Biggest: ml-pipeline 21.4 GB.",
            True,
        ),
        ResourceEvent(
            now - 3 * 3600 + 95,
            "crash",
            "critical",
            "wezterm:4242",
            "A WezTerm window closed: the kernel killed python train.py (ml-pipeline, pane 11) "
            "for lack of memory. It had grown 6.3 GB in its last 10 minutes.",
            True,
        ),
    ]


def _context(today: date):
    """webshop's sessions over yesterday: one compacts at ~190k, one stays small."""
    from datetime import datetime, timedelta

    from cardak.core.context import ContextSeries, SessionLine, group_context
    from cardak.core.resolver import Resolved

    day = datetime.combine(today - timedelta(days=1), datetime.min.time()).timestamp()

    def at(hour: float) -> float:
        return day + hour * 3600

    def climb(start: float, end: float, low: int, high: int, n: int) -> list[tuple[float, int]]:
        return [
            (
                at(start + (end - start) * i / (n - 1)),
                int(low + (high - low) * (i / (n - 1)) ** 1.3),
            )
            for i in range(n)
        ]

    main = climb(9, 12.5, 18_000, 192_000, 40) + climb(12.6, 17.5, 41_000, 142_000, 45)
    review = climb(14, 16, 12_000, 64_000, 18)
    side = climb(10, 11, 9_000, 31_000, 10)
    cwd = str(CODE / "webshop")
    series = ContextSeries(
        today,
        day + 2 * 86_400,
        (
            SessionLine("s-web", cwd, tuple(main)),
            SessionLine("s-web-side", cwd, tuple(side)),
            SessionLine("s-web-review", cwd, tuple(review)),
        ),
        {},
    )
    grouped = group_context(series, lambda c: Resolved("config", CODE / "webshop", "webshop"))
    return grouped.rows[0], today - timedelta(days=1)


def _recap(now: float):
    """The recap screen's note and git, invented: patched over `core.recap`'s readers."""
    from datetime import datetime

    from cardak.collectors.git import Commit, GitState
    from cardak.collectors.handoff import Entry
    from cardak.core.recap import Note

    body = (
        "**Done:** Coupon codes survive the payment step; the cart keeps them in the session "
        "and the end-to-end suite covers it (`tests/e2e/checkout.spec.ts`).\n\n"
        "**Open:** the e2e run needs approval for `npm run test:e2e`; Safari still drops the "
        "code on a reload.\n\n"
        "**Next:** fix the Safari reload, then open the PR for #225."
    )
    when = datetime.fromtimestamp(now - 5 * 3600).replace(second=0, microsecond=0)
    note = Note(CODE / "webshop", Entry(when, "225-checkout-e2e", body))
    commits = (
        Commit("4be81f2", now - 1500, "test(e2e): checkout with a coupon code"),
        Commit("a91c07d", now - 3 * 3600, "fix(cart): keep coupon codes across the payment step"),
        Commit("6d2f5e0", now - 4.5 * 3600, "test(e2e): the checkout's happy path"),
    )
    return note, GitState("225-checkout-e2e", 3, 1, commits)


class _Summaries:
    """The Summaries screen's settings: a local model through Ollama."""

    def summary_settings(self):
        from cardak.core.config import ProviderSettings, SummarySettings

        ollama = ProviderSettings("http://localhost:11434", "gemma4:e4b")
        return SummarySettings(5.0, "ollama", {"ollama": ollama, "codex": ProviderSettings()})

    def is_set_up(self, name: str) -> bool:
        return name in ("anthropic", "ollama", "codex")

    def has_key(self, name: str) -> bool:
        return name == "anthropic"

    def key_path(self, name: str) -> Path:
        return HOME / ".config" / "cardak" / f"{name}_key"

    def summary_spend(self) -> float:
        return 1.84

    def use_provider(self, name: str) -> None:
        return None

    def save_provider(self, name, url, model, key) -> None:
        return None

    def remove_provider(self, name: str) -> None:
        return None


def _providers():
    from cardak.core.prices import BUILTIN
    from cardak.core.summary import ProviderTools

    return ProviderTools(
        make=lambda *a: None,  # type: ignore[arg-type,return-value]
        list_models=lambda url: ["gemma4:e4b", "qwen2.5:3b", "llama3.2:3b"],
        codex_version=lambda: "codex-cli 0.160.0",
        prices=lambda: BUILTIN,
        read_key=lambda name: "k" if name == "anthropic" else None,
    )


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


def _fold(app, keep: tuple[str, ...]) -> None:
    """Fold every project but `keep`, as ← on each would."""
    for node in app.deck_tree.root.children:
        data = node.data
        if data is not None and data.group.kind == "config" and data.group.name not in keep:
            node.collapse()


def _cursor_to_pane(app, label: str) -> None:
    def walk(node):
        for child in node.children:
            if (
                child.data is not None
                and child.data.row is not None
                and child.data.row.label == label
            ):
                return child
            if (found := walk(child)) is not None:
                return found
        return None

    found = walk(app.deck_tree.root)
    if found is None:
        raise LookupError(f"no pane {label!r} in the deck")
    app.deck_tree.move_cursor(found)


def _then(*steps):
    def run(app) -> None:
        for step in steps:
            step(app)

    return run


async def _deck_svgs(out: Path, size: tuple[int, int]) -> dict[str, Path]:
    from unittest import mock

    from cardak.ui.context_plot import ContextPlotScreen

    now = time.time()
    today = date.fromtimestamp(now)
    plain = demo_snapshot(now)
    said = demo_snapshot(now, summaries=True)
    docker = demo_snapshot(now, docker=True)
    with_trends = replace(plain, usage_trends=_trends(today))
    row, day = _context(today)
    note, git = _recap(now)
    tabs = {"send": lambda *a: None, "open_tab": lambda *a: 0}
    talk = {"summaries": _Summaries(), "providers": _providers()}
    # (slide, snapshot, keys, what to do before the keys, size, the app's extra arguments)
    slides = (
        ("overview", plain, (), lambda app: _cursor_to(app, "webshop"), size, {}),
        ("waiting", plain, ("tab", "i"), None, DETAIL_SIZE, {}),
        ("summaries", said, ("w", "tab"), None, size, {}),
        ("providers", said, ("s", "down"), None, size, talk),
        ("find", plain, ("slash", *"train"), None, size, {}),
        (
            "fold",
            plain,
            (),
            _then(lambda app: _fold(app, ()), lambda app: _cursor_to(app, "webshop")),
            size,
            {},
        ),
        ("add", plain, ("a",), lambda app: _cursor_to_pane(app, "nvim"), size, {}),
        (
            "docker",
            docker,
            (),
            _then(
                lambda app: _fold(app, ("webshop", "api-gateway")),
                lambda app: _cursor_to(app, "api-gateway"),
            ),
            size,
            {},
        ),
        ("usage", with_trends, ("h", "c", "c"), None, USAGE_SIZE, {}),
        (
            "context",
            plain,
            (),
            lambda app: app.push_screen(ContextPlotScreen(row, day, today)),
            CONTEXT_SIZE,
            {},
        ),
        ("events", plain, ("e",), None, EVENTS_SIZE, {}),
        ("board", plain, ("P",), None, size, {}),
        ("recap", plain, ("r",), lambda app: _cursor_to(app, "webshop"), RECAP_SIZE, {}),
        ("cleanup", plain, ("x",), lambda app: _cursor_to(app, "webshop"), size, tabs),
        ("colors", plain, ("t",), lambda app: _cursor_to(app, "webshop"), size, {}),
    )
    made = {}
    with (
        mock.patch("cardak.core.recap.notes", lambda dirs, *a: [note]),
        mock.patch("cardak.core.recap.since", lambda n, *a: git),
    ):
        for name, snap, keys, before, own, kw in slides:
            path = out / f"{name}.svg"
            await _shoot(_app(snap, now, **kw), keys, path, own, before)
            made[name] = path
    return made


def deck_svgs(out: Path, size: tuple[int, int] = SIZE) -> dict[str, Path]:
    """The deck's slides as Textual SVG screenshots, by slide name."""
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


ART = (1970, 1026)  # the deck slides' size in CSS pixels, so every slide renders alike
MONO = "font-family:'DejaVu Sans Mono','Fira Code',monospace;white-space:pre"
WINDOWS = (
    # (project, scheme, its tabs: the active one first)
    ("webshop", "Catppuccin Mocha", ("npm run dev", "claude", "zsh")),
    ("api-gateway", "Tokyo Night", ("cargo watch -x test", "claude")),
    ("ml-pipeline", "Gruvbox Dark", ("python train.py", "claude")),
)
BODIES = {
    "webshop": (
        "~/code/webshop on 225-checkout-e2e",
        "$ npm run dev",
        "  ready on http://localhost:5173",
    ),
    "api-gateway": (
        "~/code/api-gateway on 89-retries",
        "$ cargo watch -x test",
        "  test result: ok. 214 passed",
    ),
    "ml-pipeline": (
        "~/code/ml-pipeline on main",
        "$ python train.py --epochs 40",
        "  epoch 17/40  loss 0.412",
    ),
}


def _window(i: int, project: str, scheme: str, titles: tuple[str, ...]) -> str:
    """One terminal window in its project's scheme, its tabs named as cardak.lua names them
    (plain text: cardak draws no accent of its own on a tab)."""
    bg, fg, ansi = SCHEMES[scheme]
    tabs = "".join(
        '<span style="padding:9px 18px;margin-right:2px;border-radius:8px 8px 0 0;'
        + (
            f"background:{bg};color:{fg}"
            if k == 0
            else "background:rgba(255,255,255,.06);color:#9aa0a6"
        )
        + f'">{k + 1}: {project} · {title}</span>'
        for k, title in enumerate(titles)
    )
    first, command, out = BODIES[project]
    return (
        f'<div style="position:absolute;left:{90 + i * 330}px;top:{90 + i * 290}px;width:1150px;'
        "border-radius:12px;overflow:hidden;box-shadow:0 24px 60px rgba(0,0,0,.55);"
        f'background:#16161a;{MONO};font-size:22px">'
        f'<div style="display:flex;padding:10px 10px 0;white-space:nowrap">{tabs}</div>'
        f'<div style="background:{bg};color:{fg};padding:22px 26px;height:200px;line-height:1.7">'
        f'<div style="color:{ansi[4]}">{first}</div><div>{command}</div>'
        f'<div style="color:{ansi[2]}">{out}</div></div></div>'
    )


TOML = (
    ('<span style="color:#f9e2af">[notify]</span>', ""),
    ("desktop = <b>true</b>", "notify-send"),
    ('waiting_sound = <i>"random"</i>', "a session waiting on you"),
    ('memory_sound = <i>"bell"</i>', "memory alerts, and WezTerm closed"),
    ('other_sound = <i>"chime"</i>', "every other alert"),
    ('sounds_dir = <i>"~/sounds/cardak"</i>', "your own clips join the pool"),
)


def _sounds() -> str:
    lines = "".join(
        f'<div>{code}<span style="color:#6c7086">{"  # " + note if note else ""}</span></div>'
        for code, note in TOML
    )
    note = (
        '<div style="position:absolute;right:90px;top:330px;width:640px;background:#2b2d33;'
        "color:#e8e8e8;border-radius:14px;padding:22px 26px;box-shadow:0 24px 60px rgba(0,0,0,.5);"
        'font-family:system-ui,sans-serif;font-size:22px">'
        '<div style="color:#9aa0a6;font-size:17px;margin-bottom:8px">cardak · now</div>'
        "<b>webshop · claude</b>"
        '<div style="margin-top:6px">'
        "Approve a tool: Bash: npm run test:e2e -- --project=checkout</div>"
        "</div>"
    )
    chips = "".join(
        '<span style="border:1px solid #45475a;border-radius:20px;padding:6px 16px;'
        f'margin:0 10px 10px 0;display:inline-block">♪ {name}</span>'
        for name in ("chime", "pluck", "bell", "blip", "drop", "your sound packs")
    )
    return (
        f'<div style="position:absolute;left:90px;top:250px;width:940px;background:#1e1e2e;'
        f"color:#cdd6f4;border-radius:14px;padding:34px 40px;{MONO};font-size:24px;line-height:1.9;"
        f'box-shadow:0 24px 60px rgba(0,0,0,.5)">{lines}'
        '<div style="margin-top:22px;font-size:20px;color:#a6adc8;white-space:normal">'
        f"{chips}</div></div>{note}"
    )


def illustrations(out: Path) -> dict[str, Path]:
    """Tab titles and sounds live outside the deck: drawn here as HTML, in the same invented
    projects and their schemes, at the deck slides' size."""
    out.mkdir(parents=True, exist_ok=True)
    page = (
        '<!doctype html><meta charset="utf-8"><body style="margin:0;width:{w}px;height:{h}px;'
        'background:#0c0c0c;position:relative;overflow:hidden">{body}</body>'
    )
    bodies = {
        "tabs": "".join(_window(i, *w) for i, w in enumerate(WINDOWS)),
        "sounds": _sounds(),
    }
    made = {}
    for name, body in bodies.items():
        path = out / f"{name}.html"
        path.write_text(page.format(w=ART[0], h=ART[1], body=body))
        made[name] = path
    return made


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
    os.environ["TZ"] = "UTC"  # the images show clock times: not the local zone (#106 review)
    time.tzset()
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
        for name, page in illustrations(scratch / "art").items():
            png = page.with_suffix(".png")
            _screenshot(chrome, page.as_uri(), png, *ART)
            _webp(png, OUT / f"{name}.webp")
            print(f"wrote {name}.webp")
        phone_webp(chrome, OUT, scratch)
        print("wrote phone.webp, phone-portrait.webp")


if __name__ == "__main__":
    main()
