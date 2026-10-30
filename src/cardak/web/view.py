"""The web view's JSON: a whitelist of what a snapshot shows, never the whole dataclass (M7).

New snapshot fields stay off the network until they are added here; pids never go out.
"""

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from cardak.core.agents import SessionState
from cardak.core.context import (
    TICKS,
    DayContext,
    GroupedContext,
    Point,
    ProjectContext,
    drop_indices,
)
from cardak.core.detail import shown_path, turn_text, words
from cardak.core.limits import LimitView
from cardak.core.snapshot import AgentView, DeckSnapshot, Group, PaneRow
from cardak.core.summary import TAGS
from cardak.core.trends import (
    ALL,
    DAYS,
    FIGURES,
    LABELS,
    OTHER,
    GroupedTrends,
    UsageRow,
    first_day,
    usage_edges,
    usage_rows,
    window_start,
)

Json = dict[str, Any]  # JSON-shaped; Any so callers can index nested values
MAX_POINTS = 200  # per session line on the phone (#86): the plot is 300 units wide


def _tag(agent: AgentView) -> str | None:
    return TAGS[agent.kind] if agent.status == "waiting" and agent.kind else None


def _reason(agent: AgentView) -> str | None:
    if agent.reason is None:
        return None
    return words(agent.reason, "start" if agent.kind == "permission" else "end")


def _agent(agent: AgentView | None) -> Json | None:
    if agent is None:
        return None
    return {
        "session_id": agent.session_id,
        "status": agent.status,
        "kind": agent.kind,
        "tag": _tag(agent),
        "since": agent.since,
        "summary": agent.summary,
    }


def _pane(row: PaneRow) -> Json:
    return {
        "pane_id": row.pane_id,
        "label": row.label,
        "cwd_display": row.cwd_display,
        "rss": row.rss,
        "cpu_pct": row.cpu_pct,
        "over": row.over,
        "mcp_rss": row.mcp_rss,
        "is_claude": row.is_claude,
        "agent": _agent(row.agent),
    }


def _project(g: Group) -> Json:
    return {
        "name": g.name,
        "kind": g.kind,
        "path": g.path,
        "accent": g.accent,
        "rss": g.rss,
        "cpu_pct": g.cpu_pct,
        "ram_spark": g.ram_spark,
        "over": g.over,
        "spend_usd": g.spend_usd,
        "docker": [{"label": d.label, "rss": d.rss, "cpu_pct": d.cpu_pct} for d in g.docker],
        "panes": [_pane(r) for r in g.panes],
    }


def _waiting(row: PaneRow) -> Json | None:
    a = row.agent
    if a is None:
        return None
    return {
        "session_id": a.session_id,
        "pane_id": row.pane_id,
        "project": a.project,
        "kind": a.kind,
        "tag": _tag(a),
        "since": a.since,
        "summary": a.summary,
        "reason": _reason(a),
    }


def _machine(snap: DeckSnapshot) -> Json | None:
    m = snap.machine
    if m is None:
        return None
    view = snap.machine_view
    return {
        "mem_used": m.mem_used,
        "mem_total": m.mem_total,
        "mem_available": m.mem_available,
        "swap_full": m.swap_total > 0 and m.swap_free == 0,
        "cpu_pct": m.cpu_pct,
        "level": view.level if view else None,
        "ram_spark": view.ram_spark if view else "",
        "cpu_spark": view.cpu_spark if view else "",
    }


def _limit(v: LimitView) -> Json:
    return {"left": v.left, "resets_at": v.resets_at, "level": v.level}


def _limits(snap: DeckSnapshot) -> Json | None:
    v = snap.plan_limits
    if v is None:
        return None
    return {"five_hour": _limit(v.five_hour), "seven_day": _limit(v.seven_day), "age_s": v.age_s}


def snapshot_json(snap: DeckSnapshot, ts: float, now: float) -> Json:
    waiting = [w for g in snap.groups if g.kind == "waiting" for r in g.panes if (w := _waiting(r))]
    warnings = ([snap.config_error] if snap.config_error else []) + list(snap.config_warnings)
    return {
        "ts": ts,
        "now": now,
        "error": snap.error,
        "internal": snap.internal,
        "warnings": warnings,
        "machine": _machine(snap),
        "month_spend": snap.month_spend,
        "spend_unpriced": bool(snap.spend_unpriced),
        "plan_limits": _limits(snap),
        "waiting": waiting,
        "projects": [_project(g) for g in snap.groups if g.kind != "waiting"],
    }


def find_agent(snap: DeckSnapshot, session_id: str) -> AgentView | None:
    """The session's agent view in the latest snapshot; ended sessions are not in it."""
    for g in snap.groups:
        if g.kind == "waiting":
            continue
        for r in g.panes:
            if r.agent is not None and r.agent.session_id == session_id:
                return r.agent
    return None


def session_json(agent: AgentView, state: SessionState | None, now: float) -> Json:
    prompt = state.last_prompt if state else None
    return {
        "session_id": agent.session_id,
        "project": agent.project,
        "status": agent.status,
        "kind": agent.kind,
        "tag": _tag(agent),
        "since": agent.since,
        "summary": agent.summary,
        "reason": _reason(agent),
        "last_prompt": words(prompt, "start") if prompt else None,
        "last_prompt_at": state.last_prompt_at if state else None,
        "turn": turn_text(state) if state else None,
        "turn_files": [shown_path(p, state.cwd) for p in state.turn_files] if state else [],
        "now": now,
    }


def _figure(r: UsageRow, top: float) -> Json:
    return {
        "days": list(r.days),
        "today": r.today,
        "per_day": r.per_day,
        "peak": r.peak,
        "window": r.window,
        "incomplete": r.incomplete,
        "incomplete_today": r.incomplete_today,
        "top": top if top != float("inf") else None,
    }


def thin(points: Sequence[Point]) -> list[Point]:
    """At most `MAX_POINTS` of a line: the first, the last and both sides of every drop, then
    evenly spaced others."""
    if len(points) <= MAX_POINTS:
        return list(points)
    keep = {0, len(points) - 1}
    for i in drop_indices(points):
        keep |= {i - 1, i}
    spare = MAX_POINTS - len(keep)
    if spare > 0:
        step = len(points) / spare
        keep |= {int(k * step) for k in range(spare)}
    return [points[i] for i in sorted(keep)][:MAX_POINTS]


def _day(d: DayContext | None) -> Json | None:
    if d is None:
        return None
    return {
        "peak": d.peak,
        "sessions": d.sessions,
        "drops": d.drops,
        "ended": d.ended,
        "ended_clean": d.ended_clean,
    }


def _context_row(p: ProjectContext, index: dict[str, int]) -> Json:
    kind = "all" if p.subject == ALL else "other" if p.subject == OTHER else "project"
    used = [d for d in p.days if d is not None]
    return {
        "name": p.name,
        "kind": kind,
        "peak": max((d.peak for d in used), default=None),
        "drops": sum(d.drops for d in used),
        "days": [_day(d) for d in p.days],
        "lines": [index[ln.session_id] for ln in p.lines],  # into the top-level "lines"
    }


def context_json(grouped: GroupedContext) -> Json:
    """Context per project (#86): numbers and names only. Each session's points go out once
    (All projects repeats every one), as whole seconds since the window's first midnight; no
    session id or folder goes out."""
    start = window_start(grouped.today)
    first = first_day(grouped.today)
    days = (
        datetime.combine(first + timedelta(days=i), datetime.min.time()) for i in range(DAYS + 1)
    )
    index: dict[str, int] = {}
    lines: list[list[list[int]]] = []
    for p in grouped.rows:
        for ln in p.lines:
            if ln.session_id not in index:
                index[ln.session_id] = len(lines)
                lines.append([[round(ts - start), ctx] for ts, ctx in thin(ln.points)])
    return {
        "start": start,
        "day_starts": [d.timestamp() - start for d in days],  # not i × 86,400: DST moves them
        "ticks": list(TICKS),
        "lines": lines,
        "rows": [_context_row(p, index) for p in grouped.rows],
    }


def _usage_figures(grouped: GroupedTrends) -> list[Json]:
    """Each project's figures, keyed by figure, in the grouped order."""
    rows: dict[str, Json] = {}
    for f in FIGURES:
        found = usage_rows(grouped, f)
        top = usage_edges([r for r in found if r.subject != ALL])[-1]
        top_all = usage_edges([r for r in found if r.subject == ALL])[-1]
        for r in found:
            kind = "all" if r.subject == ALL else "other" if r.subject == OTHER else "project"
            row = rows.setdefault(r.subject, {"name": r.name, "kind": kind, "figures": {}})
            row["figures"][f] = _figure(r, top_all if kind == "all" else top)
    return list(rows.values())


def trends_json(
    grouped: GroupedTrends | None,
    context: GroupedContext | None = None,
    with_context: bool = False,
) -> Json:
    """The Trends view (#88): names and numbers per project and day, nothing that identifies a
    folder or a session. Context (#86) is listed once counted but sent only `with_context`: the
    page asks while that figure is shown, and it is most of the bytes."""
    figures = [{"key": f, "label": LABELS[f]} for f in FIGURES]
    shown = context is not None and bool(context.rows)
    if shown:
        figures.append({"key": "context", "label": "context"})
    return {
        "today": grouped.today.isoformat() if grouped else None,
        "first": first_day(grouped.today).isoformat() if grouped else None,
        "figures": figures,
        "unpriced": list(grouped.unpriced) if grouped else [],
        "rows": _usage_figures(grouped) if grouped else [],
        "context": context_json(context)
        if context is not None and shown and with_context
        else None,
    }
