import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from hookfix import text

from cardak.core import hook
from cardak.core.config import Config, Project
from cardak.core.focus import FOCUS_HEADER
from cardak.core.hook import main, project_name, run, summarize_argv
from cardak.core.store import Store, StoreError

ENV = {"WEZTERM_PANE": "7"}
QUIET = '[notify]\ndesktop = true\nsound = "off"\n'


@pytest.fixture
def paths(tmp_path: Path) -> dict[str, Path]:
    config = tmp_path / "projects.toml"
    config.write_text(QUIET)
    return {
        "config_file": config,
        "focus_file": tmp_path / "focus",
        "key_file": tmp_path / "anthropic_key",
    }


def call(store: Store, paths: dict[str, Path], name: str, now: float = 100.0, **kw: object) -> list:
    spawned: list[list[str]] = []
    run(text(name, **kw), ENV, now, store=store, spawn=spawned.append, **paths)
    return spawned


def test_hook_records_and_transitions(tmp_path: Path, paths: dict[str, Path]) -> None:
    store = Store(tmp_path / "db")
    call(store, paths, "stop-finished")
    [state] = store.live_sessions()
    assert (state.status, state.kind, state.pane_id) == ("waiting", "finished", 7)
    assert store.event_count() == 1


def test_hook_pings_once(tmp_path: Path, paths: dict[str, Path]) -> None:
    store = Store(tmp_path / "db")
    first = call(store, paths, "stop-finished")
    second = call(store, paths, "stop-finished", now=101.0)
    assert [argv[0] for argv in first] == ["notify-send"]
    assert first[0][3] == "demo · claude"
    assert second == []


def test_hook_no_ping_when_pane_focused(tmp_path: Path, paths: dict[str, Path]) -> None:
    store = Store(tmp_path / "db")
    paths["focus_file"].write_text(f"{FOCUS_HEADER}\n7 1000\n")
    assert call(store, paths, "stop-finished") == []
    call(store, paths, "user_prompt_submit", now=101.0)
    paths["focus_file"].write_text(f"{FOCUS_HEADER}\n7 1000\nnone 2000\n")
    assert len(call(store, paths, "stop-finished", now=102.0)) == 1


def test_hook_bad_config_still_pings(tmp_path: Path, paths: dict[str, Path]) -> None:
    paths["config_file"].write_text("[[broken")
    spawned = call(Store(tmp_path / "db"), paths, "stop-finished")
    assert spawned[0][0] == "notify-send" and spawned[0][3] == "demo · claude"


def test_hook_ignores_garbage(tmp_path: Path, paths: dict[str, Path]) -> None:
    store = Store(tmp_path / "db")
    run("nope", ENV, 1.0, store=store, spawn=lambda argv: None, **paths)
    assert store.live_sessions() == [] and store.event_count() == 0


def test_session_start_prunes(tmp_path: Path, paths: dict[str, Path]) -> None:
    store = Store(tmp_path / "db")
    store.record_event("old", "Stop", "{}", 1.0)
    call(store, paths, "session_start", now=30 * 86_400.0)
    assert store.event_count() == 1


def test_large_payloads_are_capped(tmp_path: Path, paths: dict[str, Path]) -> None:
    store = Store(tmp_path / "db")
    call(store, paths, "post_tool_use-bash", tool_response={"stdout": "x" * 100_000})
    row = store._con.execute("SELECT payload_json FROM events").fetchone()  # noqa: SLF001
    assert len(row[0]) <= hook.MAX_PAYLOAD


def test_project_name() -> None:
    config = Config(
        projects=(
            Project("repos", Path("/r")),
            Project("web", Path("/r/web")),
        )
    )
    assert project_name(Path("/r/web/src"), config) == "web"
    assert project_name(Path("/r/other"), config) == "repos"
    assert project_name(Path("/elsewhere/thing"), config) == "thing"
    assert project_name(Path("/r/webshop"), config) == "repos"
    assert project_name(None, config) == "claude"


def env_for(tmp_path: Path) -> dict[str, str]:
    config = tmp_path / "projects.toml"
    config.write_text('[notify]\ndesktop = false\nsound = "off"\n')
    return {
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),  # never the real key file
        "CARDAK_CONFIG": str(config),
        **ENV,
    }


def test_main_is_silent_and_records(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(io.StringIO(text("stop-finished")), env_for(tmp_path)) == 0
    assert capsys.readouterr() == ("", "")
    assert len(Store(tmp_path / "state" / "cardak" / "cardak.db").live_sessions()) == 1


def test_hook_garbage_is_silent(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(io.StringIO("nope"), env_for(tmp_path)) == 0
    assert capsys.readouterr() == ("", "")
    assert Store(tmp_path / "state" / "cardak" / "cardak.db").live_sessions() == []


def test_hook_exception_is_logged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken(*_a: object, **_k: object) -> None:
        raise StoreError("disk on fire")

    monkeypatch.setattr(hook, "_record", broken)
    env = env_for(tmp_path)
    assert main(io.StringIO(text("stop-finished")), env) == 0
    assert capsys.readouterr() == ("", "")
    log = tmp_path / "state" / "cardak" / "hook.log"
    assert "disk on fire" in log.read_text()

    monkeypatch.setattr(hook, "LOG_MAX", 10)
    main(io.StringIO("{}"), env)
    assert log.read_text().count("\n--- ") + log.read_text().startswith("--- ") == 1  # started over


def test_main_survives_an_unwritable_state_dir(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "state").write_text("not a directory")
    env = env_for(tmp_path)
    assert main(io.StringIO(json.dumps({"x": 1})), env) == 0
    assert capsys.readouterr() == ("", "")


def test_ping_spawns_the_summarizer(tmp_path: Path, paths: dict[str, Path]) -> None:
    store = Store(tmp_path / "db")
    paths["key_file"].write_text("k")
    spawned = call(store, paths, "stop-finished", now=1792600000.123456)
    [state] = store.live_sessions()
    argv = spawned[-1]
    assert argv[:3] == [sys.executable, "-P", "-c"]
    assert argv[4:6] == ["summarize", state.session_id]
    assert float(argv[6]) == state.status_since


def test_no_summarizer_without_key(tmp_path: Path, paths: dict[str, Path]) -> None:
    spawned = call(Store(tmp_path / "db"), paths, "stop-finished")
    assert [argv[0] for argv in spawned] == ["notify-send"]


def test_no_summarizer_when_disabled(tmp_path: Path, paths: dict[str, Path]) -> None:
    paths["key_file"].write_text("k")
    paths["config_file"].write_text(QUIET + "[summary]\nmonthly_limit_usd = 0\n")
    spawned = call(Store(tmp_path / "db"), paths, "stop-finished")
    assert [argv[0] for argv in spawned] == ["notify-send"]


def test_no_summarizer_when_pane_focused(tmp_path: Path, paths: dict[str, Path]) -> None:
    paths["key_file"].write_text("k")
    paths["focus_file"].write_text(f"{FOCUS_HEADER}\n7 1000\n")
    assert call(Store(tmp_path / "db"), paths, "stop-finished") == []


def test_summarize_argv_runs_the_cli(tmp_path: Path) -> None:
    config = tmp_path / "p.toml"
    config.write_text(QUIET)
    env = os.environ | {
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "CARDAK_CONFIG": str(config),
    }
    result = subprocess.run(
        summarize_argv("nope", 1.0), env=env, capture_output=True, text=True, check=False
    )
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")
    assert not (tmp_path / "state" / "cardak" / "hook.log").exists()


def test_summarize_argv_ignores_the_working_directory(tmp_path: Path) -> None:
    # review: `python -c` put the Claude session's project dir first on sys.path
    project = tmp_path / "project"
    project.mkdir()
    marker = tmp_path / "ran"
    (project / "json.py").write_text(f"open({str(marker)!r}, 'w').close()\nfrom json import *\n")
    config = tmp_path / "p.toml"
    config.write_text(QUIET)
    env = os.environ | {
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "CARDAK_CONFIG": str(config),
    }
    result = subprocess.run(
        summarize_argv("nope", 1.0), env=env, cwd=project, capture_output=True, text=True,
        check=False,
    )  # fmt: skip
    assert result.returncode == 0, result.stderr
    assert not marker.exists()


def hook_log(tmp_path: Path) -> str:
    log = tmp_path / "state" / "cardak" / "hook.log"
    return log.read_text() if log.exists() else ""


def test_garbage_creates_no_db_and_logs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(io.StringIO("nope"), env_for(tmp_path)) == 0
    assert not (tmp_path / "state" / "cardak" / "cardak.db").exists()
    assert "ignored: unparseable payload (4 bytes): 'nope'" in hook_log(tmp_path)
    assert capsys.readouterr() == ("", "")


def test_empty_stdin_logs(tmp_path: Path) -> None:
    assert main(io.StringIO(""), env_for(tmp_path)) == 0
    assert "ignored: unparseable payload (0 bytes): ''" in hook_log(tmp_path)


def test_unknown_event_logged_and_recorded(tmp_path: Path) -> None:
    payload = json.dumps({"hook_event_name": "BrandNewEvent", "session_id": "s1"})
    assert main(io.StringIO(payload), env_for(tmp_path)) == 0
    assert "ignored: unknown event BrandNewEvent" in hook_log(tmp_path)
    assert Store(tmp_path / "state" / "cardak" / "cardak.db").event_count() == 1


def test_subagent_stop_not_logged(tmp_path: Path) -> None:
    payload = json.dumps({"hook_event_name": "SubagentStop", "session_id": "s1"})
    assert main(io.StringIO(payload), env_for(tmp_path)) == 0
    assert hook_log(tmp_path) == ""


def test_quiet_ping_spawns_summary_only(tmp_path: Path, paths: dict[str, Path]) -> None:
    store = Store(tmp_path / "db")
    paths["key_file"].write_text("k")
    first = call(store, paths, "permission_request", now=100.0)
    assert [argv[0] for argv in first][0] == "notify-send"
    second = call(
        store, paths, "notification-permission_prompt", now=101.0,
        notification_type="elicitation_dialog", message="Fill in the form",
    )  # fmt: skip
    [state] = store.live_sessions()
    assert len(second) == 1 and second[0][4:6] == ["summarize", state.session_id]
    assert float(second[0][6]) == state.status_since == 101.0


def test_env_for_hides_the_real_key(tmp_path: Path) -> None:
    from cardak.core.config import anthropic_key_path

    assert anthropic_key_path(env_for(tmp_path)).is_relative_to(tmp_path)


def test_project_name_follows_symlinks(tmp_path: Path) -> None:
    real = tmp_path / "disk" / "web"
    (real / "src").mkdir(parents=True)
    link = tmp_path / "web"
    link.symlink_to(real)
    config = Config(projects=(Project(name="webshop", path=link),))
    assert project_name(real / "src", config) == "webshop"  # Claude Code reports the real cwd
    config = Config(projects=(Project(name="webshop", path=real),))
    assert project_name(link / "src", config) == "webshop"
