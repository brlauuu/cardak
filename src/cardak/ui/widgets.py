"""Row labels for the deck tree."""

import math
import time
from datetime import datetime

from rich.color import Color, ColorParseError
from rich.text import Text

from cardak.core.agents import KIND_LABELS
from cardak.core.limits import STALE_S, LimitsView, LimitView
from cardak.core.snapshot import AgentView, DockerLine, Group, MachineView, PaneRow
from cardak.core.summary import TAGS

WAITING = "dark_orange"
WORKING = "green"
IDLE = "grey50"
OVER = "bold red"
MAX_REASON = 120


def fmt_cpu(cpu: float | None) -> str:
    return "–" if cpu is None else f"{cpu:.0f}%"


def fmt_rss(rss: int) -> str:
    gib = rss / 2**30
    return f"{gib:.1f}G" if gib >= 1 else f"{rss / 2**20:.0f}M"


def fmt_usd(usd: float) -> str:
    """ "$0.42" under $10, then whole dollars: "$37", "$1,204"; nothing at all is "$0", and
    less than a cent "<$0.01" (#114)."""
    if usd == 0:
        return "$0"
    if usd < 0.005:
        return "<$0.01"
    return f"${usd:.2f}" if usd < 10 else f"${usd:,.0f}"


COUNT_UNITS = (("", 1.0), ("k", 1e3), ("M", 1e6), ("B", 1e9))


def fmt_count(n: float) -> str:
    """ "999", "1.2k", "340k", "1.2M", "3.4B": one decimal under 10 of a unit. Rounded half up
    first, so 999.5k is "1.0M", not "1000k" (#115); the page's `count` does the same sums."""
    for unit, size in COUNT_UNITS:
        v = n / size
        tenths = math.floor(v * 10 + 0.5) / 10
        if unit and tenths < 10:
            return f"{tenths:.1f}{unit}"
        whole = math.floor(v + 0.5)
        if whole < 1000 or unit == "B":
            return f"{whole}{unit}"
    raise AssertionError("unreachable")


def share(rss: int, total: int | None) -> str:
    """ " (32%)": rss as a share of the machine's RAM (MemTotal); "" with no total to compare."""
    if not total:
        return ""
    pct = rss / total * 100
    # half up, like the web view's Math.round (an f-string rounds 0.5 to 0)
    return " (<1%)" if 0 < pct < 0.5 else f" ({math.floor(pct + 0.5)}%)"


Row = tuple[Text, Text]  # a line's left part and its columns (#122)

# (heading, cells) per column; values are right-aligned in theirs (#122). RAM is the size and
# its share, then the graph (`core/recent.spark`, 8 cells) left-aligned after it.
RAM_VALUE, SPARK = 12, 8
COLUMNS: tuple[tuple[str, int], ...] = (
    ("state", 8),
    ("RAM", RAM_VALUE + 1 + SPARK),
    ("CPU", 5),
    ("ctx", 5),
    ("month", 7),
)
GAP = 2  # cells between the left part and the first column
COLUMNS_WIDTH = GAP + sum(w for _, w in COLUMNS) + len(COLUMNS) - 1


def _cell(text: Text, width: int) -> Text:
    text = text.copy()
    text.truncate(width, overflow="ellipsis")
    return Text(" " * (width - text.cell_len)).append_text(text)


def _join(cells: list[Text]) -> Text:
    out = Text(" " * GAP)
    for i, cell in enumerate(cells):
        if i:
            out.append(" ")
        out.append_text(cell)
    return out


def columns(
    state: Text | None,
    rss: int | None,
    cpu: float | None,
    *,
    total: int | None,
    spark: str = "",
    over: bool = False,
    ctx: int | None = None,
    month: float | None = None,
) -> Text:
    """A row's columns: state, RAM (with its share and graph), CPU, ctx, month. Exactly
    COLUMNS_WIDTH cells; None leaves a column blank. Red when over a threshold."""
    style = OVER if over else ""
    widths = [w for _, w in COLUMNS]
    ram = Text() if rss is None else Text(f"{fmt_rss(rss)}{share(rss, total)}", style=style)
    cpu_text = Text() if rss is None and cpu is None else Text(fmt_cpu(cpu), style=style)
    graph = Text(spark[:SPARK], style="cyan")
    graph.append(" " * (SPARK - graph.cell_len))
    return _join([
        _cell(state or Text(), widths[0]),
        _cell(ram, RAM_VALUE).append(" ").append_text(graph),
        _cell(cpu_text, widths[2]),
        _cell(Text() if not ctx else Text(fmt_count(ctx), style="dim"), widths[3]),
        _cell(Text() if month is None else Text(fmt_usd(month), style="dim"), widths[4]),
    ])  # fmt: skip


def heading() -> Text:
    """The columns' names, dim, laid out like `columns`."""
    cells = [_cell(Text(name), w) for name, w in COLUMNS]
    cells[1] = _cell(Text(COLUMNS[1][0]), RAM_VALUE).append(" " * (1 + SPARK))  # over the size
    text = _join(cells)
    text.stylize("dim")
    return text


def fit_line(row: Row, width: int) -> Text:
    """The left part padded or cut (with …) so the columns end at `width` cells; only the
    columns when there's no room left for it."""
    left, right = row
    room = width - right.cell_len
    if room <= 0:
        return right.copy()
    out = left.copy()
    out.truncate(room, overflow="ellipsis", pad=True)
    return out.append_text(right)


def mcp_row(r: PaneRow, total: int | None = None) -> Row:
    return Text("    └ MCP servers", style="dim"), columns(
        None, r.mcp_rss, r.mcp_cpu_pct, total=total
    )


def docker_row(line: DockerLine, total: int | None = None) -> Row:
    return Text(f"    {line.label}"), columns(None, line.rss, line.cpu_pct, total=total)


def outside_row(g: Group, total: int | None = None) -> Row:
    return Text("    outside WezTerm", style="dim"), columns(
        None, g.outside_rss or 0, g.outside_cpu_pct, total=total
    )


def machine_label(v: MachineView) -> Text:
    m = v.sample
    gib = 2**30
    text = Text(f"CPU {fmt_cpu(m.cpu_pct)} ")
    text.append(v.cpu_spark, style="cyan")
    text.append(
        f"   RAM {m.mem_used / gib:.1f} / {m.mem_total / gib:.1f}G{share(m.mem_used, m.mem_total)} "
    )
    text.append(v.ram_spark, style="cyan")
    if v.level is not None:
        free = round(m.mem_available / m.mem_total * 100)
        text.append(f"  ⚠ {free}% free", style=OVER if v.level == "critical" else "yellow")
    if m.swap_total > 0 and m.swap_free == 0:
        text.append("  swap full", style=OVER)
    return text


LIMIT_BAR = 8  # cells per limit's bar: both share the top bar with the counts (#121)
LIMIT_STYLE = {"ok": "cyan", "low": "yellow", "critical": OVER, "unknown": "dim"}


def until(seconds: float) -> str:
    """Time left, rounded down: <1m, 59m, 2h10m, 2d21h (the page's limitUntil, #121)."""
    s = int(seconds)
    if s < 60:
        return "<1m"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86_400:
        h, m = divmod(s // 60, 60)
        return f"{h}h{m}m" if m else f"{h}h"
    d, h = divmod(s // 3600, 24)
    return f"{d}d{h}h" if h else f"{d}d"


def _reset_text(resets_at: float, now: float) -> str:
    """4h40m (16:40) within a day, 2d21h (Mon 09:00) further out (local time)."""
    when = datetime.fromtimestamp(resets_at)
    clock = f"{when:%H:%M}" if resets_at - now < 86_400 else f"{when:%a %H:%M}"
    return f"{until(resets_at - now)} ({clock})"


def _limit_part(name: str, v: LimitView, now: float) -> Text:
    if v.used is None or v.resets_at is None:
        return Text(f"{name} —", style=LIMIT_STYLE["unknown"])
    full = int(v.used * LIMIT_BAR / 100 + 0.5)  # halves up, as the page's Math.round
    style = LIMIT_STYLE[v.level]
    text = Text(f"{name} ")
    text.append("█" * full + "░" * (LIMIT_BAR - full), style=style)
    text.append(f" {v.used}%", style=style if v.level != "ok" else "")
    return text.append(f" · {_reset_text(v.resets_at, now)}")


def limits_label(v: LimitsView, now: float) -> Text:
    """ "5h ██░░ 27% · 4h40m (16:40)   week █████░ 61% · 2d21h (Mon 09:00)": both, filling up."""
    text = (
        _limit_part("5h", v.five_hour, now)
        .append("   ")
        .append(_limit_part("week", v.seven_day, now))
    )
    if v.age_s > STALE_S:
        text.append(f" · read {int(v.age_s / 60 + 0.5)} min ago", style="dim")
    return text


def _valid_color(color: str | None) -> str | None:
    if not color:
        return None
    try:
        Color.parse(color)
    except ColorParseError:
        return None
    return color


def age(seconds: float) -> str:
    if seconds < 60:
        return "<1m"
    if seconds < 3600:
        return f"{seconds // 60:.0f}m"
    if seconds < 86_400:
        return f"{seconds // 3600:.0f}h"
    return f"{seconds // 86_400:.0f}d"


def agent_dot(row: PaneRow) -> Text:
    """● waiting / ● working / ○ idle; · for a Claude pane with no hook record yet."""
    if not row.is_claude:
        return Text()
    agent = row.agent
    if agent is None:
        return Text("· ", style="dim")
    if agent.status == "waiting":
        return Text("● ", style=f"bold {WAITING}")
    if agent.status == "working":
        return Text("● ", style=WORKING)
    return Text("○ ", style=IDLE)


def _cut(text: str, limit: int = MAX_REASON) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _reason(agent: AgentView) -> str:
    label = KIND_LABELS[agent.kind] if agent.kind else "Waiting"
    return _cut(" ".join(f"{label}: {agent.reason or ''}".split()).rstrip(":"))


def _why(agent: AgentView) -> Text:
    """The waiting tag and summary when there is one, else the kind and raw reason."""
    if agent.summary and agent.kind:
        tag = TAGS[agent.kind]
        summary = _cut(agent.summary, MAX_REASON - len(tag) - 2)
        return Text(tag, style=f"bold {WAITING}").append(f"  {summary}")
    return Text(_reason(agent))


def group_row(g: Group, total: int | None = None) -> Row:
    text = Text()
    if g.kind == "waiting":
        return Text(g.name, style=f"bold {WAITING}"), Text()
    if g.kind == "config":
        color = _valid_color(g.accent)
        text.append(g.name, style=f"bold {color}" if color else "bold")
    elif g.kind == "git":
        text.append(g.name, style="bold dim")
        text.append(" · not in config", style="dim italic")
    else:
        text.append(g.name, style="bold")
    if g.path:
        text.append(f"  {g.path}", style="dim")
    month = g.spend_usd or None
    right = columns(
        None, g.rss, g.cpu_pct, total=total, spark=g.ram_spark, over=g.over, month=month
    )
    return text, right


def _state(agent: AgentView | None, now: float) -> Text | None:
    if agent is None:
        return None
    if agent.status == "waiting":
        return Text(f"wait {age(now - agent.since)}", style=WAITING)
    if agent.status == "working":
        return Text("work", style=WORKING)
    return Text(f"idle {age(now - agent.since)}", style="dim")


def pane_row(r: PaneRow, now: float | None = None, total: int | None = None) -> Row:
    now = time.time() if now is None else now
    text = Text("▲ ", style=OVER) if r.over else Text()
    text.append_text(agent_dot(r))
    text.append(r.label, style="bold")
    if r.detail:
        text.append(f"  {r.detail}")
    text.append(f"  {r.cwd_display}", style="dim")
    agent = r.agent
    if agent and agent.status == "working" and agent.current_tool:
        text.append(f"  {agent.current_tool}", style="dim")
    if r.is_self:
        text.append("  (this deck)", style="italic")
    right = columns(
        _state(agent, now), r.rss, r.cpu_pct, total=total, spark=r.ram_spark, over=r.over,
        ctx=agent.context if agent else None,
    )  # fmt: skip
    return text, right


def reason_label(r: PaneRow) -> Text:
    """The second line under a waiting session in its project group."""
    if r.agent is None:
        return Text("", style=WAITING)
    return Text("    ↳ ", style=WAITING).append_text(_why(r.agent))


def waiting_head(r: PaneRow, now: float | None = None) -> Text:
    """A wait's first part: its dot, project · claude, and how long."""
    now = time.time() if now is None else now
    text = agent_dot(r)
    if r.agent is None:
        return text.append(r.label, style="bold")
    text.append(f"{r.agent.project} · claude", style="bold")
    return text.append(f"  {age(now - r.agent.since)}", style=WAITING)


def waiting_reason(r: PaneRow) -> Text:
    """A wait's why: the tag and summary, else the kind and raw reason."""
    return Text() if r.agent is None else _why(r.agent)


def waiting_label(r: PaneRow, now: float | None = None) -> Text:
    """A row of the "Waiting on you" group: project, how long, and why."""
    text = waiting_head(r, now)
    if r.agent is None:
        return text
    return text.append("  ").append_text(waiting_reason(r))


def unreachable_label(reason: str) -> Text:
    return Text(f"WezTerm not reachable: {reason} · retrying", style="bold red")


def internal_label(reason: str) -> Text:
    return Text(f"cardak hit an internal error: {reason} · retrying", style="bold red")


def nothing_waiting_label() -> Text:
    return Text("Nobody is waiting on you.", style="dim italic")


def no_match_label(query: str) -> Text:
    return Text(f'No pane matches "{query}"', style="dim italic")
