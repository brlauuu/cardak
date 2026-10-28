import json
from datetime import date
from pathlib import Path

from cardak.collectors.machine import MachineSample
from cardak.core.agents import SessionState
from cardak.core.snapshot import AgentView, DeckSnapshot, DockerLine, Group, MachineView, PaneRow
from cardak.core.trends import DAYS, DayUsage, GroupedTrends, ProjectTrend
from cardak.web.view import find_agent, session_json, snapshot_json, trends_json

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
