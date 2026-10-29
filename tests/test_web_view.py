import json
from datetime import date, datetime, timedelta
from pathlib import Path

from cardak.collectors.machine import MachineSample
from cardak.core.agents import SessionState
from cardak.core.context import GroupedContext, ProjectContext, SessionLine, day_context
from cardak.core.snapshot import AgentView, DeckSnapshot, DockerLine, Group, MachineView, PaneRow
from cardak.core.trends import DAYS, DayUsage, GroupedTrends, ProjectTrend
from cardak.web.view import (
    MAX_POINTS,
    context_json,
    find_agent,
    session_json,
    snapshot_json,
    thin,
    trends_json,
)

CWD = Path("/home/user/repos/webshop")
ASK = AgentView(
    "s1",
    "waiting",
    "permission",
    100.0,
    "Bash: npm run migrate",
    "Bash",
    "webshop",
    summary="Wants to run the migrations",
)


def row(pane_id: int, agent: AgentView | None = None, label: str = "zsh") -> PaneRow:
    return PaneRow(
        pane_id,
        label,
        None,
        CWD,
        "~/repos/webshop",
        2.0,
        300,
        False,
        is_claude=agent is not None,
        agent=agent,
        pids=frozenset({4242}),
    )


def snap() -> DeckSnapshot:
    claude, shell = row(1, ASK, "claude"), row(2)
    waiting = Group("waiting", None, "Waiting on you", None, None, 0.0, 0, (claude,))
    project = Group(
        "config",
        CWD,
        "webshop",
        "#c678dd",
        "~/repos/webshop",
        3.0,
        600,
        (claude, shell),
        docker=(DockerLine("docker: webshop-db-1", 50, 0.5),),
        ram_spark="▁▂",
    )
    other = Group("other", None, "Other", None, None, 1.0, 900, ())
    m = MachineSample(16 * 2**30, 6 * 2**30, 0, 0, 18.0, 8)
    return DeckSnapshot(
        (waiting, project, other),
        2,
        n_waiting=1,
        machine=m,
        machine_view=MachineView(m, "warning", "▁▃", "▂▂"),
        config_warnings=("duplicate path",),
    )


def test_snapshot_json_shape() -> None:
    out = snapshot_json(snap(), 100.0, 112.0)
    assert out["ts"] == 100.0 and out["now"] == 112.0 and out["error"] is None
    assert out["warnings"] == ["duplicate path"]
    w = out["waiting"][0]
    assert w == {
        "session_id": "s1",
        "pane_id": 1,
        "project": "webshop",
        "kind": "permission",
        "tag": "approve",
        "since": 100.0,
        "summary": "Wants to run the migrations",
        "reason": "Bash: npm run migrate",
    }
    names = [p["name"] for p in out["projects"]]
    assert names == ["webshop", "Other"]
    web = out["projects"][0]
    assert web["accent"] == "#c678dd" and web["rss"] == 600 and web["ram_spark"] == "▁▂"
    assert web["docker"] == [{"label": "docker: webshop-db-1", "rss": 50, "cpu_pct": 0.5}]
    assert web["panes"][0]["agent"]["tag"] == "approve"
    assert web["panes"][1]["agent"] is None
    assert out["machine"]["mem_used"] == 10 * 2**30 and out["machine"]["level"] == "warning"
    json.dumps(out)


def test_snapshot_json_never_leaks_pids() -> None:
    text = json.dumps(snapshot_json(snap(), 1.0, 2.0))
    assert "pids" not in text and "4242" not in text and "is_self" not in text


def test_snapshot_json_error_snapshot() -> None:
    out = snapshot_json(DeckSnapshot(error="no WezTerm GUI socket found"), 1.0, 2.0)
    assert out["error"] == "no WezTerm GUI socket found"
    assert out["projects"] == [] and out["waiting"] == [] and out["machine"] is None


def test_session_json_cuts_and_relativises() -> None:
    state = SessionState(
        "s1",
        pane_id=1,
        cwd=CWD,
        status="waiting",
        kind="permission",
        last_prompt=" ".join(["word"] * 150),
        last_prompt_at=90.0,
        turn_edits=2,
        turn_files=(str(CWD / "src/app.py"), "/etc/hosts"),
    )
    out = session_json(ASK, state, 112.0)
    assert out["project"] == "webshop" and out["tag"] == "approve"
    assert out["summary"] == "Wants to run the migrations"
    assert out["last_prompt"].endswith("…") and len(out["last_prompt"].split()) == 100
    assert out["turn"] == "2 edits"
    assert out["turn_files"] == ["src/app.py", "/etc/hosts"]
    assert out["now"] == 112.0
    json.dumps(out)


def test_session_json_without_state() -> None:
    out = session_json(ASK, None, 1.0)
    assert out["last_prompt"] is None and out["turn_files"] == []


def test_find_agent_ignores_waiting_group_duplicates() -> None:
    s = snap()
    assert find_agent(s, "s1") == ASK
    assert find_agent(s, "nope") is None
    only_waiting = DeckSnapshot((s.groups[0],))
    assert find_agent(only_waiting, "s1") is None


def test_snapshot_json_spend() -> None:
    from dataclasses import replace

    base = snap()
    groups = (base.groups[0], replace(base.groups[1], spend_usd=37.5), base.groups[2])
    spent = replace(base, groups=groups, month_spend=412.0, spend_unpriced=("claude-x",))
    out = snapshot_json(spent, 1.0, 2.0)
    assert out["month_spend"] == 412.0 and out["spend_unpriced"] is True
    assert [p["spend_usd"] for p in out["projects"]] == [37.5, None]
    blank = snapshot_json(base, 1.0, 2.0)
    assert blank["month_spend"] is None and blank["spend_unpriced"] is False
    json.dumps(out)


def grouped() -> GroupedTrends:
    d = DayUsage(cost=2.0, unpriced=1, tokens=500, output_tokens=50, replies=2, prompts=1,
                 prompt_chars=12, sessions=frozenset({"secret-session"}))  # fmt: skip
    days = (None,) * (DAYS - 1) + (d,)
    big = (None,) * (DAYS - 1) + (DayUsage(cost=9.0, replies=1),)
    return GroupedTrends(
        date(2026, 10, 28),
        (
            ProjectTrend("project:/home/user/webshop", "webshop", days),
            ProjectTrend("other", "Other", days),
            ProjectTrend("all", "All projects", big),
        ),
        ("claude-x",),
    )


def test_trends_json_shape() -> None:
    out = trends_json(grouped())
    assert out["today"] == "2026-10-28" and out["first"] == "2026-09-29"
    assert [f["key"] for f in out["figures"]][:2] == ["cost", "tokens"]
    assert out["unpriced"] == ["claude-x"]
    assert [(r["name"], r["kind"]) for r in out["rows"]] == [
        ("webshop", "project"), ("Other", "other"), ("All projects", "all")
    ]  # fmt: skip
    cost = out["rows"][0]["figures"]["cost"]
    assert len(cost["days"]) == DAYS and cost["days"][-1] == 2.0 and cost["days"][0] is None
    assert cost["today"] == 2.0 and cost["incomplete"] and cost["incomplete_today"]
    assert cost["top"] == 2.0  # the busiest project or Other day
    assert out["rows"][2]["figures"]["cost"]["top"] == 9.0  # All projects: its own
    assert out["rows"][0]["figures"]["prompt_len"]["window"] == 12.0
    text = json.dumps(out)
    for leak in ("/home/user", "secret-session", "subject", "cwd"):
        assert leak not in text, leak


def test_trends_json_none() -> None:
    out = trends_json(None)
    assert out["rows"] == [] and out["today"] is None


def context(points: int = 3) -> GroupedContext:
    today = date(2026, 10, 28)
    noon = datetime.combine(today, datetime.min.time()).timestamp() + 12 * 3600
    line = SessionLine(
        "secret-session", "/home/user/webshop", tuple((noon + i, 50_000 + i) for i in range(points))
    )
    days = day_context([line], today, noon + 7200)
    rows = (
        ProjectContext("project:/home/user/webshop", "webshop", (line,), days),
        ProjectContext("all", "All projects", (line,), days),
    )
    return GroupedContext(today, noon + 7200, rows)


def test_context_json_days_and_lines() -> None:
    out = context_json(context())
    start = datetime(2026, 9, 29).timestamp()
    assert out["start"] == start and out["ticks"] == [10_000, 100_000, 1_000_000]
    web, every = out["rows"]
    assert (web["name"], web["kind"], every["kind"]) == ("webshop", "project", "all")
    assert web["peak"] == 50_002 and web["drops"] == 0
    assert len(web["days"]) == DAYS and web["days"][0] is None
    assert web["days"][-1] == {
        "peak": 50_002,
        "sessions": 1,
        "drops": 0,
        "ended": 1,
        "ended_clean": 1,
    }
    first = round(datetime(2026, 10, 28, 12).timestamp() - start)
    assert out["lines"] == [[[first, 50_000], [first + 1, 50_001], [first + 2, 50_002]]]
    assert web["lines"] == every["lines"] == [0]  # each session's points go out once


def test_context_json_has_no_ids_or_paths() -> None:
    text = json.dumps(context_json(context()))
    for leak in ("/home/user", "secret-session", "subject", "cwd"):
        assert leak not in text, leak


def test_context_lines_are_capped_but_keep_drops() -> None:
    pts = [(float(i), 300_000 if i % 1000 < 700 else 60_000) for i in range(2000)]
    kept = thin(pts)
    assert len(kept) <= MAX_POINTS
    for i in (700, 1700):  # 300k → 60k: both sides of each drop stay
        assert pts[i - 1] in kept and pts[i] in kept
    assert kept[0] == pts[0] and kept[-1] == pts[-1]
    assert thin(pts[:10]) == pts[:10]


def test_trends_json_lists_context_only_with_data() -> None:
    assert "context" not in [f["key"] for f in trends_json(grouped())["figures"]]
    out = trends_json(grouped(), context())
    assert out["figures"][-1] == {"key": "context", "label": "context"}
    assert out["context"] is None  # listed, but sent only when asked for
    asked = trends_json(grouped(), context(), with_context=True)
    assert asked["context"]["rows"][0]["name"] == "webshop"
    assert trends_json(grouped(), with_context=True)["context"] is None


def test_busy_month_stays_small() -> None:
    today = date(2026, 10, 28)
    start = datetime.combine(today, datetime.min.time()).timestamp() - 20 * 86_400
    lines = [
        SessionLine(f"s{i}", f"/home/user/p{i % 5}",
                    tuple((start + i * 30_000 + k * 37.123, 40_000 + k * 150) for k in range(2000)))
        for i in range(60)
    ]  # fmt: skip
    rows = tuple(
        ProjectContext(f"project:/home/user/p{j}", f"p{j}", tuple(lines[j::5]), (None,) * DAYS)
        for j in range(5)
    ) + (ProjectContext("all", "All projects", tuple(lines), (None,) * DAYS),)
    out = context_json(GroupedContext(today, start + 30 * 86_400, rows))
    size = len(json.dumps(out, separators=(",", ":")))  # as the server sends it
    assert size < 300_000, size


def test_context_json_sends_each_local_midnight() -> None:
    # a day is not always 86,400 s (the Oct 25 DST change): the page cuts days where these say
    out = context_json(context())
    first = date(2026, 9, 29)
    midnights = [
        datetime.combine(first, datetime.min.time()) + timedelta(days=i) for i in range(DAYS + 1)
    ]
    assert out["day_starts"] == [m.timestamp() - out["start"] for m in midnights]
