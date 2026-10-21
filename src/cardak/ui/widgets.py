"""Row labels for the deck tree."""

import time

from rich.color import Color, ColorParseError
from rich.text import Text

from cardak.core.agents import KIND_LABELS
from cardak.core.snapshot import AgentView, Group, PaneRow

WAITING = "dark_orange"
WORKING = "green"
IDLE = "grey50"
MAX_REASON = 120


def fmt_cpu(cpu: float | None) -> str:
    return "–" if cpu is None else f"{cpu:.0f}%"


def fmt_rss(rss: int) -> str:
    gib = rss / 2**30
    return f"{gib:.1f}G" if gib >= 1 else f"{rss / 2**20:.0f}M"


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


def _reason(agent: AgentView) -> str:
    label = KIND_LABELS[agent.kind] if agent.kind else "Waiting"
    text = " ".join(f"{label}: {agent.reason or ''}".split()).rstrip(":")
    return text if len(text) <= MAX_REASON else text[: MAX_REASON - 1] + "…"


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
    text.append(f"  CPU {fmt_cpu(g.cpu_pct)}  RAM {fmt_rss(g.rss)}")
    return text


def pane_label(r: PaneRow, now: float | None = None) -> Text:
    now = time.time() if now is None else now
    text = agent_dot(r)
    text.append(r.label, style="bold")
    if r.detail:
        text.append(f"  {r.detail}")
    text.append(f"  {r.cwd_display}", style="dim")
    text.append(f"  CPU {fmt_cpu(r.cpu_pct)}  RAM {fmt_rss(r.rss)}")
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
    return Text(f"    ↳ {_reason(r.agent)}" if r.agent else "", style=WAITING)


def waiting_label(r: PaneRow, now: float | None = None) -> Text:
    """A row of the "Waiting on you" group: project, how long, and why."""
    now = time.time() if now is None else now
    text = agent_dot(r)
    if r.agent is None:
        return text.append(r.label, style="bold")
    text.append(f"{r.agent.project} · claude", style="bold")
    text.append(f"  {age(now - r.agent.since)}", style=WAITING)
    text.append(f"  {_reason(r.agent)}")
    return text


def unreachable_label(reason: str) -> Text:
    return Text(f"WezTerm not reachable: {reason} — retrying", style="bold red")


def nothing_waiting_label() -> Text:
    return Text("Nobody is waiting on you.", style="dim italic")


def no_match_label(query: str) -> Text:
    return Text(f'No pane matches "{query}"', style="dim italic")
