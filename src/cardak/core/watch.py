"""Resource alerts from successive snapshots: sustain timers, quiet rules, culprit text. Pure."""

from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Literal

from cardak.collectors.machine import MachineSample
from cardak.core.resources import GIB, Thresholds, memory_level
from cardak.core.snapshot import DeckSnapshot, Group, PaneRow

AlertKind = Literal["memory", "big", "growth", "busy", "machine_busy"]
Level = Literal["warning", "critical"]
MEMORY_SUSTAIN_S = 10.0
LOG_EVERY_S = 60.0  # a condition that persists is logged (not notified) at most once a minute
PROJECT_KINDS = ("config", "git")


@dataclass(frozen=True)
class ResourceAlert:
    ts: float
    kind: AlertKind
    level: Level
    subject: str  # "machine" | "project:<path>" | "pane:<id>"
    title: str
    text: str
    notify: bool


def gb(n: int) -> str:
    return f"{n / GIB:.1f} GB"


@dataclass(frozen=True)
class _Subject:
    """A project, or a pane in no project: what an alert can blame."""

    key: str
    name: str
    rss: int
    cpu: float
    group: Group | None = None
    pane: PaneRow | None = None


def _pane_rss(r: PaneRow) -> int:
    return r.rss + r.mcp_rss


def _pane_cpu(r: PaneRow) -> float:
    return (r.cpu_pct or 0.0) + r.mcp_cpu_pct


def _subjects(snap: DeckSnapshot) -> list[_Subject]:
    out: list[_Subject] = []
    for g in snap.groups:
        if g.kind in PROJECT_KINDS:
            out.append(_Subject(f"project:{g.key}", g.name, g.rss, g.cpu_pct, group=g))
        elif g.kind == "other":
            out += [
                _Subject(
                    f"pane:{r.pane_id}", f"pane {r.pane_id}", _pane_rss(r), _pane_cpu(r), pane=r
                )
                for r in g.panes
            ]
    return out


def _pieces(g: Group, by_cpu: bool) -> Iterator[tuple[float, str]]:
    for r in g.panes:
        yield (_pane_cpu(r) if by_cpu else _pane_rss(r)), f"{r.label} in pane {r.pane_id}"
    for d in g.docker:
        yield ((d.cpu_pct or 0.0) if by_cpu else d.rss), d.label


def _ram(s: _Subject) -> str:
    """What the subject holds: its biggest piece out of its total, or the pane's own size."""
    if s.group is None:
        assert s.pane is not None
        return f"{s.name} · {s.pane.label} · {gb(s.rss)}"
    size, piece = max(_pieces(s.group, by_cpu=False), default=(0, "no panes"))
    return f"{s.name} · {piece} · {gb(int(size))} of the project's {gb(s.rss)}"


def _cpu(s: _Subject, total: bool = True) -> str:
    if s.group is None:
        assert s.pane is not None
        return f"{s.name} · {s.pane.label} · {s.cpu:.0f}% CPU"
    cpu, piece = max(_pieces(s.group, by_cpu=True), default=(0.0, "no panes"))
    tail = f" of the project's {s.cpu:.0f}%" if total else ""
    return f"{s.name} · {piece} · {cpu:.0f}% CPU{tail}"


def _used(m: MachineSample) -> str:
    return f" · RAM {round(m.mem_used / m.mem_total * 100)}% used"


def _memory_culprit(snap: DeckSnapshot, subjects: list[_Subject]) -> str:
    candidates = [(s.rss, _ram(s)) for s in subjects]
    for g in snap.groups:
        if g.kind == "other":
            candidates += [(d.rss, f"{d.label} · {gb(d.rss)}") for d in g.docker]
            candidates.append((g.outside_rss or 0, "most of it is outside WezTerm"))
    return max(candidates, default=(0, "nothing in WezTerm"))[1]


def _memory_text(snap: DeckSnapshot, m: MachineSample, subjects: list[_Subject]) -> str:
    free = round(m.mem_available / m.mem_total * 100)
    swap = ", swap full" if m.swap_total > 0 and m.swap_free == 0 else ""
    culprit = _memory_culprit(snap, subjects)
    return (
        f"{gb(m.mem_available)} free of {gb(m.mem_total)} ({free}% free){swap} · biggest: {culprit}"
    )


@dataclass
class _Condition:
    since: float | None = None
    notified_at: float | None = None
    notified_level: Level | None = None
    logged_at: float | None = None


@dataclass
class Watcher:
    _conds: dict[tuple[str, AlertKind], _Condition] = field(default_factory=dict)
    _series: dict[str, deque[tuple[float, int]]] = field(default_factory=dict)

    def _hold(self, key: tuple[str, AlertKind], active: bool, now: float) -> float:
        """How long the condition has held (0 when it doesn't)."""
        cond = self._conds.setdefault(key, _Condition())
        if not active:
            cond.since = None
            return 0.0
        if cond.since is None:
            cond.since = now
        return now - cond.since

    def _fire(
        self,
        key: tuple[str, AlertKind],
        level: Level,
        now: float,
        t: Thresholds,
        title: str,
        text: Callable[[], str],
    ) -> ResourceAlert | None:
        cond = self._conds[key]
        escalated = level == "critical" and cond.notified_level == "warning"
        due = cond.notified_at is None or now - cond.notified_at >= t.repeat_s
        if due or escalated:
            cond.notified_at, cond.notified_level, cond.logged_at = now, level, now
            notify = True
        elif cond.logged_at is None or now - cond.logged_at >= LOG_EVERY_S:
            cond.logged_at = now
            notify = False
        else:
            return None
        return ResourceAlert(now, key[1], level, key[0], title, text(), notify)

    def _growth(self, s: _Subject, t: Thresholds, now: float) -> float | None:
        """GB/min over the sustain window, once the window is covered."""
        series = self._series.setdefault(s.key, deque())
        series.append((now, s.rss))
        while len(series) > 1 and series[1][0] <= now - t.sustain_s:
            series.popleft()  # keep the newest sample at least sustain_s old
        then, rss = series[0]
        if now - then < t.sustain_s:
            return None
        return (s.rss - rss) / GIB / ((now - then) / 60)

    def _forget(self, present: set[str]) -> None:
        for key, cond in self._conds.items():
            if key[0] not in present:
                cond.since = None  # the notify clock stays: a reused pane id stays quiet
        for key in set(self._series) - present:
            del self._series[key]

    def _subject_alerts(
        self, s: _Subject, m: MachineSample, t: Thresholds, now: float
    ) -> Iterator[ResourceAlert | None]:
        big = (s.key, "big")
        self._hold(big, s.rss > t.pane_ram, now)
        if s.rss > t.pane_ram:  # at once: no sustain
            yield self._fire(
                big, "warning", now, t, f"cardak · {s.name} is big", lambda: _ram(s) + _used(m)
            )
        rate = self._growth(s, t, now)  # already measured over the sustain window
        growing = rate is not None and rate * GIB >= t.growth_per_min
        growth = (s.key, "growth")
        self._hold(growth, growing, now)
        if growing:
            yield self._fire(
                growth,
                "warning",
                now,
                t,
                f"cardak · {s.name} is growing fast",
                lambda: f"{_ram(s)}, +{rate:.1f} GB/min{_used(m)}",
            )
        busy = (s.key, "busy")
        if self._hold(busy, s.cpu > t.pane_cpu_pct, now) >= t.sustain_s:
            yield self._fire(
                busy, "warning", now, t, f"cardak · {s.name} is busy", lambda: _cpu(s) + _used(m)
            )

    def _machine_alerts(
        self,
        snap: DeckSnapshot,
        m: MachineSample,
        subjects: list[_Subject],
        t: Thresholds,
        now: float,
    ) -> Iterator[ResourceAlert | None]:
        level = memory_level(m, t)
        held = self._hold(("machine", "memory"), level is not None, now)
        if level == "critical" or (level == "warning" and held >= MEMORY_SUSTAIN_S):
            title = f"cardak · memory {'critical' if level == 'critical' else 'tight'}"
            yield self._fire(
                ("machine", "memory"), level, now, t, title, lambda: _memory_text(snap, m, subjects)
            )
        hot = m.cpu_pct is not None and m.cpu_pct > t.machine_cpu_pct
        if self._hold(("machine", "machine_busy"), hot, now) >= t.sustain_s:
            busiest = max(subjects, key=lambda s: s.cpu, default=None)
            tail = f" · busiest: {_cpu(busiest, total=False)}" if busiest else ""
            minutes = f"{t.sustain_s / 60:.0f}"
            yield self._fire(
                ("machine", "machine_busy"),
                "warning",
                now,
                t,
                "cardak · machine busy",
                lambda: f"CPU {m.cpu_pct:.0f}% for {minutes} min{tail}",
            )

    def step(self, snap: DeckSnapshot, t: Thresholds, now: float) -> list[ResourceAlert]:
        m = snap.machine
        if m is None:
            return []
        subjects = _subjects(snap)
        self._forget({"machine", *(s.key for s in subjects)})
        alerts = list(self._machine_alerts(snap, m, subjects, t, now))
        for s in subjects:
            alerts += self._subject_alerts(s, m, t, now)
        return [a for a in alerts if a is not None]
