"""Row labels for the deck tree."""

import math
import time

from rich.color import Color, ColorParseError
from rich.text import Text

from cardak.core.agents import KIND_LABELS
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
    """ "$0.42" under $10, then whole dollars: "$37", "$1,204"; nothing at all is "$0"."""
    if usd == 0:
        return "$0"
    return f"${usd:.2f}" if usd < 10 else f"${usd:,.0f}"


def fmt_count(n: float) -> str:
    """ "999", "1.2k", "340k", "1.2M", "3.4B": one decimal under 10 of a unit."""
    for unit, size in (("B", 1e9), ("M", 1e6), ("k", 1e3)):
        if n >= size:
            return f"{n / size:.1f}{unit}" if n < 10 * size else f"{n / size:.0f}{unit}"
    return f"{n:.0f}"


def share(rss: int, total: int | None) -> str:
    """ " (32%)": rss as a share of the machine's RAM (MemTotal); "" with no total to compare."""
    if not total:
        return ""
    pct = rss / total * 100
    # half up, like the web view's Math.round (an f-string rounds 0.5 to 0)
    return " (<1%)" if 0 < pct < 0.5 else f" ({math.floor(pct + 0.5)}%)"


def usage(
    rss: int,
    cpu_pct: float | None,
    spark: str = "",
    over: bool = False,
    total: int | None = None,
) -> Text:
    """ "RAM 13.9G (32%) ▁▂▄▆█  CPU 180%"; red when over a threshold."""
    style = OVER if over else ""
    text = Text(f"RAM {fmt_rss(rss)}{share(rss, total)}", style=style)
    if spark:
        text.append(f" {spark}", style="cyan")
    return text.append(f"  CPU {fmt_cpu(cpu_pct)}", style=style)


def mcp_label(r: PaneRow, total: int | None = None) -> Text:
    text = Text("    └ MCP servers  ", style="dim")
    return text.append_text(usage(r.mcp_rss, r.mcp_cpu_pct, total=total))


def docker_label(line: DockerLine, total: int | None = None) -> Text:
    return Text(f"    {line.label}  ").append_text(usage(line.rss, line.cpu_pct, total=total))


def outside_label(g: Group, total: int | None = None) -> Text:
    text = Text("    outside WezTerm  ", style="dim")
    return text.append_text(usage(g.outside_rss or 0, g.outside_cpu_pct, total=total))


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


def group_label(g: Group, total: int | None = None) -> Text:
    text = Text()
    if g.kind == "waiting":
        return Text(g.name, style=f"bold {WAITING}")
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
    text.append("  ")
    if g.spend_usd:
        text.append(f"{fmt_usd(g.spend_usd)} this month", style="dim").append("  ")
    return text.append_text(usage(g.rss, g.cpu_pct, g.ram_spark, g.over, total))


def pane_label(r: PaneRow, now: float | None = None, total: int | None = None) -> Text:
    now = time.time() if now is None else now
    text = Text("▲ ", style=OVER) if r.over else Text()
    text.append_text(agent_dot(r))
    text.append(r.label, style="bold")
    if r.detail:
        text.append(f"  {r.detail}")
    text.append(f"  {r.cwd_display}  ", style="dim")
    text.append_text(usage(r.rss, r.cpu_pct, r.ram_spark, r.over, total))
    if r.is_self:
        text.append("  (this deck)", style="italic")
    if agent := r.agent:
        if agent.status == "waiting":
            text.append(f"  waiting {age(now - agent.since)}", style=WAITING)
        elif agent.status == "working":
            if agent.current_tool:
                text.append(f"  {agent.current_tool}", style="dim")
        else:
            text.append(f"  idle {age(now - agent.since)}", style="dim")
        if agent.context:
            text.append(f"  ctx {fmt_count(agent.context)}", style="dim")
    return text


def reason_label(r: PaneRow) -> Text:
    """The second line under a waiting session in its project group."""
    if r.agent is None:
        return Text("", style=WAITING)
    return Text("    ↳ ", style=WAITING).append_text(_why(r.agent))


def waiting_label(r: PaneRow, now: float | None = None) -> Text:
    """A row of the "Waiting on you" group: project, how long, and why."""
    now = time.time() if now is None else now
    text = agent_dot(r)
    if r.agent is None:
        return text.append(r.label, style="bold")
    text.append(f"{r.agent.project} · claude", style="bold")
    text.append(f"  {age(now - r.agent.since)}", style=WAITING)
    text.append("  ")
    return text.append_text(_why(r.agent))


def unreachable_label(reason: str) -> Text:
    return Text(f"WezTerm not reachable: {reason} · retrying", style="bold red")


def internal_label(reason: str) -> Text:
    return Text(f"cardak hit an internal error: {reason} · retrying", style="bold red")


def nothing_waiting_label() -> Text:
    return Text("Nobody is waiting on you.", style="dim italic")


def no_match_label(query: str) -> Text:
    return Text(f'No pane matches "{query}"', style="dim italic")
