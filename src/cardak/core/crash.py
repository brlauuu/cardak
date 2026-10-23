"""What led to a WezTerm window closing: the GUI watch and the report text. Pure.

The daemon sees each GUI pid (its `gui-sock-<pid>`) every ~2 s. When one disappears, it waits
CHECK_AFTER_S for the journal to catch up, then judges it: an OOM kill in that GUI's scope, or
the scope failing with 'oom-kill', is loud (a notification and the deck's banner); anything else
is one quiet line in the event log.
"""

from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from cardak.collectors.journal import JournalEntry, OomKill, failed_scopes, oom_kills
from cardak.collectors.machine import MachineSample
from cardak.core.config import ResourceSettings
from cardak.core.resources import GIB, Thresholds, derive, memory_level
from cardak.core.snapshot import DeckSnapshot
from cardak.core.watch import gb

CHECK_AFTER_S = 5.0
JOURNAL_BEFORE_S = 120.0
KEEP_S = 600.0  # each pane's RAM over the last 10 minutes: "grew from … in N minutes"
PID_KEEP_S = 60.0
CRITICAL_BEFORE_S = 30.0
WEZTERM_SCOPE = "app-org.wezfurlong.wezterm-"
PROJECT_KINDS = ("config", "git")
POLICY = (
    "systemd then stopped the whole WezTerm window, because its policy stops an app when one of "
    "its processes is killed (`OOMPolicy=stop`)."
)
FIX = (
    "To keep the window open next time, run `cardak resources install` and add the drop-in it "
    "prints."
)


@dataclass(frozen=True)
class Gone:
    pid: int
    scope: str | None
    ts: float


@dataclass(frozen=True)
class CrashReport:
    loud: bool
    title: str
    text: str


@dataclass(frozen=True)
class _PaneInfo:
    project: str | None  # None: a pane in no project
    label: str


def _when(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S on %b %-d")


def _total(m: MachineSample) -> str:
    return f"{m.mem_total / GIB:.0f} GB"


def _biggest(snap: DeckSnapshot | None) -> str | None:
    if snap is None:
        return None
    users: list[tuple[int, str]] = []
    for g in snap.groups:
        if g.kind in PROJECT_KINDS:
            users.append((g.rss, g.name))
        elif g.kind == "other":
            users += [(r.rss + r.mcp_rss, f"pane {r.pane_id} · {r.label}") for r in g.panes]
            users += [(d.rss, d.label) for d in g.docker]
            if g.outside_rss:
                users.append((g.outside_rss, "outside WezTerm"))
    top = sorted(users, key=lambda u: -u[0])[:5]
    if not top:
        return None
    listed = ", ".join(f"{name} {gb(rss)}" for rss, name in top)
    return (
        f"The {len(top)} biggest users then: {listed}."
        if len(top) > 1
        else (f"The biggest user then: {listed}.")
    )


class CrashWatch:
    def __init__(self) -> None:
        self._guis: dict[int, str | None] = {}
        self._pending: list[Gone] = []
        self._series: dict[int, deque[tuple[float, int]]] = {}
        self._pids: dict[int, tuple[int, float]] = {}  # pid → (pane id, last seen)
        self._panes: dict[int, _PaneInfo] = {}
        self._machine: deque[tuple[float, MachineSample]] = deque()
        self._last: DeckSnapshot | None = None

    def observe(self, guis: Mapping[int, str | None], snap: DeckSnapshot, now: float) -> None:
        for pid, scope in self._guis.items():
            if pid not in guis:
                self._pending.append(Gone(pid, scope, now))
        self._guis = {pid: scope or self._guis.get(pid) for pid, scope in guis.items()}
        if snap.machine is not None:
            self._machine.append((now, snap.machine))
        while self._machine and self._machine[0][0] < now - KEEP_S:
            self._machine.popleft()
        if snap.error is None:
            self._last = snap
            self._record_panes(snap, now)
        for key, series in list(self._series.items()):
            while series and series[0][0] < now - KEEP_S:
                series.popleft()
            if not series:
                del self._series[key]
        self._pids = {p: seen for p, seen in self._pids.items() if seen[1] >= now - PID_KEEP_S}

    def _record_panes(self, snap: DeckSnapshot, now: float) -> None:
        for g in snap.groups:
            if g.kind == "waiting":
                continue
            project = g.name if g.kind in PROJECT_KINDS else None
            for r in g.panes:
                self._panes[r.pane_id] = _PaneInfo(project, r.label)
                self._series.setdefault(r.pane_id, deque()).append((now, r.rss))
                for pid in r.pids:
                    self._pids[pid] = (r.pane_id, now)

    def due(self, now: float) -> list[Gone]:
        ready = [g for g in self._pending if now - g.ts >= CHECK_AFTER_S]
        self._pending = [g for g in self._pending if now - g.ts < CHECK_AFTER_S]
        return ready

    def _relevant(self, gone: Gone, kernel: Sequence[JournalEntry]) -> list[OomKill]:
        def ours(k: OomKill) -> bool:
            if gone.scope is not None:
                return k.memcg.endswith("/" + gone.scope)
            return WEZTERM_SCOPE in k.memcg

        return [
            k
            for k in oom_kills(kernel)
            if gone.ts - JOURNAL_BEFORE_S <= k.ts <= gone.ts + 10 and ours(k)
        ]

    def _killed(self, k: OomKill) -> str:
        seen = self._pids.get(k.pid)
        series = self._series.get(seen[0]) if seen else None
        if seen is None or not series:
            return (
                f"The kernel killed `{k.task}` (pid {k.pid}), which cardak didn't see in a "
                "WezTerm pane."
            )
        info = self._panes[seen[0]]
        where = f"{info.project}, pane {seen[0]}" if info.project else f"pane {seen[0]}"
        minutes = max(1, round((series[-1][0] - series[0][0]) / 60))
        unit = "minute" if minutes == 1 else "minutes"
        return (
            f"The kernel killed `{k.task}` (pid {k.pid}) in {where}, which grew from "
            f"{gb(series[0][1])} to {gb(max(rss for _, rss in series))} in {minutes} {unit}."
        )

    def _critical_before(self, gone: Gone, t: Thresholds | None) -> MachineSample | None:
        for ts, m in reversed(self._machine):
            if ts < gone.ts - CRITICAL_BEFORE_S:
                break
            limits = t or derive(ResourceSettings(), m.mem_total, m.n_cpus)
            if memory_level(m, limits) == "critical":
                return m
        return None

    def report(
        self,
        gone: Gone,
        kernel: Sequence[JournalEntry] | None,
        user: Sequence[JournalEntry],
        t: Thresholds | None,
    ) -> CrashReport:
        kills = self._relevant(gone, kernel) if kernel is not None else []
        failed = gone.scope is not None and gone.scope in failed_scopes(user)
        critical = self._critical_before(gone, t) if kernel is None else None
        biggest = _biggest(self._last)
        if not (kills or failed or critical):
            why = (
                "the journal wasn't readable" if kernel is None else "the journal shows no OOM kill"
            )
            text = f"WezTerm (pid {gone.pid}) closed at {_when(gone.ts)}; {why}."
            return CrashReport(
                False, "cardak · WezTerm closed", " ".join([text, biggest or ""]).strip()
            )
        sentences = [f"WezTerm was closed at {_when(gone.ts)}."]
        last = self._machine[-1][1] if self._machine else None
        if critical is not None:
            sentences.append(
                "The journal wasn't readable, but memory was critical just before "
                f"({gb(critical.mem_available)} free of {_total(critical)})."
            )
        elif last is not None:
            swap = "; swap full" if last.swap_total > 0 and last.swap_free == 0 else ""
            sentences.append(f"The machine ran out of memory ({_total(last)}{swap}).")
        if kills:
            sentences.append(self._killed(max(kills, key=lambda k: k.anon_rss or 0)))
        if failed:
            sentences.append(POLICY)
        if biggest:
            sentences.append(biggest)
        if failed:
            sentences.append(FIX)
        return CrashReport(True, "cardak · WezTerm closed: out of memory", " ".join(sentences))
