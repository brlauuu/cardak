"""Row labels for the deck tree."""

from rich.color import Color, ColorParseError
from rich.text import Text

from cardak.core.snapshot import Group, PaneRow


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


def group_label(g: Group) -> Text:
    text = Text()
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


def pane_label(r: PaneRow) -> Text:
    text = Text()
    text.append(r.label, style="bold")
    if r.detail:
        text.append(f"  {r.detail}")
    text.append(f"  {r.cwd_display}", style="dim")
    text.append(f"  CPU {fmt_cpu(r.cpu_pct)}  RAM {fmt_rss(r.rss)}")
    if r.is_self:
        text.append("  (this deck)", style="italic")
    return text


def unreachable_label(reason: str) -> Text:
    return Text(f"WezTerm not reachable: {reason} — retrying", style="bold red")
