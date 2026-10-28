"""The web view's JSON: a whitelist of what a snapshot shows, never the whole dataclass (M7).

New snapshot fields stay off the network until they are added here; pids never go out.
"""

from typing import Any

from cardak.core.agents import SessionState
from cardak.core.detail import shown_path, turn_text, words
from cardak.core.snapshot import AgentView, DeckSnapshot, Group, PaneRow
from cardak.core.summary import TAGS

Json = dict[str, Any]  # JSON-shaped; Any so callers can index nested values


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
