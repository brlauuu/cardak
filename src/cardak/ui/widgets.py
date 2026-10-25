"""Row labels for the deck tree."""

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


def usage(rss: int, cpu_pct: float | None, spark: str = "", over: bool = False) -> Text:
    """ "RAM 13.9G ▁▂▄▆█  CPU 180%"; red when over a threshold."""
    style = OVER if over else ""
    text = Text(f"RAM {fmt_rss(rss)}", style=style)
    if spark:
        text.append(f" {spark}", style="cyan")
    return text.append(f"  CPU {fmt_cpu(cpu_pct)}", style=style)


def mcp_label(r: PaneRow) -> Text:
    return Text("    └ MCP servers  ", style="dim").append_text(usage(r.mcp_rss, r.mcp_cpu_pct))


def docker_label(line: DockerLine) -> Text:
    return Text(f"    {line.label}  ").append_text(usage(line.rss, line.cpu_pct))


def outside_label(g: Group) -> Text:
    text = Text("    outside WezTerm  ", style="dim")
    return text.append_text(usage(g.outside_rss or 0, g.outside_cpu_pct))


def machine_label(v: MachineView) -> Text:
    m = v.sample
    gib = 2**30
    text = Text(f"CPU {fmt_cpu(m.cpu_pct)} ")
    text.append(v.cpu_spark, style="cyan")
    text.append(f"   RAM {m.mem_used / gib:.1f} / {m.mem_total / gib:.1f}G ")
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


def group_label(g: Group) -> Text:
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
    return text.append_text(usage(g.rss, g.cpu_pct, g.ram_spark, g.over))


def pane_label(r: PaneRow, now: float | None = None) -> Text:
    now = time.time() if now is None else now
    text = Text("▲ ", style=OVER) if r.over else Text()
    text.append_text(agent_dot(r))
    text.append(r.label, style="bold")
    if r.detail:
        text.append(f"  {r.detail}")
    text.append(f"  {r.cwd_display}  ", style="dim")
    text.append_text(usage(r.rss, r.cpu_pct, r.ram_spark, r.over))
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
