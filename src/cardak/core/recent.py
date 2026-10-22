"""The last few minutes of usage, in memory: sparklines and "over a threshold" marks. Pure."""

import math
from collections import deque
from collections.abc import Sequence
from dataclasses import replace

from cardak.core.resources import Thresholds, is_over, memory_level
from cardak.core.snapshot import DeckSnapshot, Group, MachineView, PaneRow

BARS = "▁▂▃▄▅▆▇█"
MACHINE = "machine"


def spark(values: Sequence[float | None], top: float, width: int = 8) -> str:
    """Newest on the right; each bar the max of its bucket; an all-None bucket is blank."""
    if top <= 0 or not values:
        return ""
    size = max(1, math.ceil(len(values) / width))
    bars: list[str] = []
    end = len(values)
    while end > 0 and len(bars) < width:
        bucket = [v for v in values[max(0, end - size) : end] if v is not None]
        bars.append(BARS[min(7, int(max(bucket) / top * 8))] if bucket else " ")
        end -= size
    return "".join(reversed(bars))


def group_key(g: Group) -> str:
    return f"g:{g.kind}:{g.key}"


def pane_key(r: PaneRow) -> str:
    return f"p:{r.pane_id}"


class Recent:
    def __init__(self, keep: int = 120) -> None:  # 120 polls ≈ 4 min at 2 s
        self._keep = keep
        self._series: dict[str, deque[tuple[float, float | None]]] = {}

    def _add(self, key: str, rss: float, cpu: float | None, seen: set[str]) -> None:
        if key in seen:
            return  # a pane listed twice (waiting group + project) is one sample
        seen.add(key)
        self._series.setdefault(key, deque(maxlen=self._keep)).append((rss, cpu))

    def record(self, snap: DeckSnapshot) -> None:
        seen: set[str] = set()
        for g in snap.groups:
            if g.kind != "waiting":
                self._add(group_key(g), g.rss, g.cpu_pct, seen)
            for r in g.panes:
                cpu = None if r.cpu_pct is None else r.cpu_pct + r.mcp_cpu_pct
                self._add(pane_key(r), r.rss + r.mcp_rss, cpu, seen)
        if m := snap.machine:
            self._add(MACHINE, m.mem_used, m.cpu_pct, seen)
        for key in set(self._series) - seen:
            del self._series[key]

    def ram(self, key: str) -> list[float]:
        return [rss for rss, _ in self._series.get(key, ())]

    def cpu(self, key: str) -> list[float | None]:
        return [cpu for _, cpu in self._series.get(key, ())]


def _row(r: PaneRow, recent: Recent, t: Thresholds) -> PaneRow:
    cpu = None if r.cpu_pct is None else r.cpu_pct + r.mcp_cpu_pct
    return replace(
        r,
        ram_spark=spark(recent.ram(pane_key(r)), t.pane_ram),
        over=is_over(r.rss + r.mcp_rss, cpu, t),
    )


def _group(g: Group, recent: Recent, t: Thresholds) -> Group:
    rows = tuple(_row(r, recent, t) for r in g.panes)
    if g.kind == "waiting":
        return replace(g, panes=rows)
    return replace(
        g,
        panes=rows,
        ram_spark=spark(recent.ram(group_key(g)), t.pane_ram),
        over=g.kind != "other" and is_over(g.rss, g.cpu_pct, t),  # Other is never a culprit
    )


def decorate(snap: DeckSnapshot, recent: Recent, t: Thresholds) -> DeckSnapshot:
    groups = tuple(_group(g, recent, t) for g in snap.groups)
    view = None
    if m := snap.machine:
        view = MachineView(
            sample=m,
            level=memory_level(m, t),
            ram_spark=spark(recent.ram(MACHINE), m.mem_total),
            cpu_spark=spark(recent.cpu(MACHINE), 100.0),
        )
    return replace(snap, groups=groups, machine_view=view)
