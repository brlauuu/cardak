import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from hookfix import text

from cardak.core import hook
from cardak.core.config import Config, SummarySettings
from cardak.core.store import Store, SummaryRow
from cardak.core.summarize import main, run
from cardak.core.summary import MODEL, SYSTEM_PROMPT

FIXTURES = Path(__file__).parent / "fixtures" / "summaries"
QUIET = '[notify]\ndesktop = false\nsound = "off"\n'


@dataclass
class R:
    text: str | None
    stop_reason: str | None = "end_turn"
    model: str = MODEL
    input_tokens: int = 1500
    output_tokens: int = 30


@dataclass
class Stub:
    reply: R = field(default_factory=lambda: R("Did x; nothing asked"))
    calls: list[tuple[str, str]] = field(default_factory=list)
    keys: list[str] = field(default_factory=list)

    def make(self, key: str) -> "Stub":
        self.keys.append(key)
        return self

    def summarize(self, system: str, user: str) -> R:
        self.calls.append((system, user))
        return self.reply


@pytest.fixture
def setup(tmp_path: Path) -> tuple[Store, Path, Path]:
    config = tmp_path / "projects.toml"
    config.write_text(QUIET)
    key = tmp_path / "anthropic_key"
    key.write_text("sk-test\n")
    key.chmod(0o600)
    return Store(tmp_path / "db"), config, key


def seed(store: Store, config: Path, *steps: tuple[str, dict]) -> tuple[str, float]:
    """Run hook events at t = 100, 101, …; return the session id and its status_since."""
    for i, (name, overrides) in enumerate(steps):
        hook.run(
            text(name, **overrides),
            {"WEZTERM_PANE": "7"},
            100.0 + i,
            store=store,
            config_file=config,
            focus_file=config.parent / "focus",
            key_file=config.parent / "no-key",
            spawn=lambda _argv: None,
        )
    [state] = store.live_sessions()
    return state.session_id, state.status_since


def mid_turn(store: Store, config: Path) -> tuple[str, float]:
    return seed(
        store,
        config,
        ("stop-finished", {"last_assistant_message": "PREVIOUS TURN TEXT"}),
        ("user_prompt_submit", {"prompt": "fix the retry bug"}),
        ("pre_tool_use-bash", {"tool_input": {"command": "cargo test"}}),
        (
            "permission_request",
            {
                "tool_input": {"command": "cargo test"},
                "transcript_path": str(FIXTURES / "mid-turn.jsonl"),
            },
        ),
    )


def do(store: Store, key: Path, stub: Stub, sid: str, since: float, config: Config | None = None):
    run(
        sid,
        since,
        since + 1,
        store=store,
        config=config or Config(),
        key_file=key,
        make_summarizer=stub.make,
    )


def test_writes_a_summary_for_a_finished_turn(setup: tuple[Store, Path, Path]) -> None:
    store, config, key = setup
    sid, since = seed(store, config, ("stop-finished", {"last_assistant_message": "All done."}))
    stub = Stub()
    do(store, key, stub, sid, since)
    assert store.summaries_for([(sid, since)]) == {sid: "Did x; nothing asked"}
    assert stub.keys == ["sk-test"]
    system, user = stub.calls[0]
    assert system == SYSTEM_PROMPT
    assert "All done." in user and "PENDING TOOL" not in user


def test_mid_turn_wait_never_sends_previous_last_message(setup: tuple[Store, Path, Path]) -> None:
    store, config, key = setup
    sid, since = mid_turn(store, config)
    stub = Stub()
    do(store, key, stub, sid, since)
    _, user = stub.calls[0]
    assert "PREVIOUS TURN TEXT" not in user
    assert "CURRENT TURN TEXT" in user
    assert "cargo test" in user and "PENDING TOOL" in user


def test_mid_turn_wait_without_transcript(setup: tuple[Store, Path, Path], tmp_path: Path) -> None:
    store, config, key = setup
    sid, since = seed(
        store,
        config,
        ("stop-finished", {"last_assistant_message": "PREVIOUS TURN TEXT"}),
        ("user_prompt_submit", {}),
        ("permission_request", {"transcript_path": str(tmp_path / "absent.jsonl")}),
    )
    stub = Stub()
    do(store, key, stub, sid, since)
    _, user = stub.calls[0]
    assert "PREVIOUS TURN TEXT" not in user and "touch made.txt" in user


def test_truncated_event_payload_falls_back_to_reason(
    setup: tuple[Store, Path, Path], tmp_path: Path
) -> None:
    store, config, key = setup
    sid, since = mid_turn(store, config)
    con = sqlite3.connect(tmp_path / "db")
    con.execute(
        "UPDATE events SET payload_json = substr(payload_json, 1, 50) WHERE ts = ?", (since,)
    )
    con.commit()
    con.close()
    stub = Stub()
    do(store, key, stub, sid, since)
    _, user = stub.calls[0]
    assert "Bash: cargo test" in user  # state.reason, the tool summary M4 stored
    assert store.has_summary(sid, since)


def test_skips_when_the_wait_changed(setup: tuple[Store, Path, Path]) -> None:
    store, config, key = setup
    sid, since = seed(store, config, ("stop-finished", {}))
    stub = Stub()
    do(store, key, stub, sid, since + 1)
    assert stub.calls == [] and not store.has_summary(sid, since + 1)


def test_skips_when_not_waiting(setup: tuple[Store, Path, Path]) -> None:
    store, config, key = setup
    sid, since = seed(store, config, ("stop-finished", {}), ("user_prompt_submit", {}))
    stub = Stub()
    do(store, key, stub, sid, since)
    assert stub.calls == []


def test_skips_an_unknown_session(setup: tuple[Store, Path, Path]) -> None:
    store, _config, key = setup
    stub = Stub()
    do(store, key, stub, "nope", 1.0)
    assert stub.calls == []


def test_skips_when_already_summarized(setup: tuple[Store, Path, Path]) -> None:
    store, config, key = setup
    sid, since = seed(store, config, ("stop-finished", {}))
    stub = Stub()
    do(store, key, stub, sid, since)
    do(store, key, stub, sid, since)
    assert len(stub.calls) == 1


def test_skips_when_disabled(setup: tuple[Store, Path, Path]) -> None:
    store, config, key = setup
    sid, since = seed(store, config, ("stop-finished", {}))
    stub = Stub()
    do(store, key, stub, sid, since, Config(summary=SummarySettings(0)))
    assert stub.calls == []


def test_skips_without_key_or_with_open_key(setup: tuple[Store, Path, Path]) -> None:
    store, config, key = setup
    sid, since = seed(store, config, ("stop-finished", {}))
    stub = Stub()
    key.chmod(0o644)
    do(store, key, stub, sid, since)
    key.unlink()
    do(store, key, stub, sid, since)
    assert stub.calls == []


def test_skips_over_the_cap(setup: tuple[Store, Path, Path]) -> None:
    store, config, key = setup
    sid, since = seed(store, config, ("stop-finished", {}))
    store.add_summary(SummaryRow("other", 1.0, "x", MODEL, 0, 0, 5.0, since))
    stub = Stub()
    do(store, key, stub, sid, since)
    assert stub.calls == []


def test_rejected_output_is_recorded_without_text(setup: tuple[Store, Path, Path]) -> None:
    store, config, key = setup
    sid, since = seed(store, config, ("stop-finished", {}))
    stub = Stub(R("a\nb"))
    do(store, key, stub, sid, since)
    assert store.has_summary(sid, since)
    assert store.summaries_for([(sid, since)]) == {}
    assert store.spend_since(0.0) == pytest.approx(0.000165)


def env_for(tmp_path: Path) -> dict[str, str]:
    config = tmp_path / "projects.toml"
    if not config.exists():
        config.write_text(QUIET)
    return {
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "CARDAK_CONFIG": str(config),
    }


def test_main_logs_api_errors_silently(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env = env_for(tmp_path)
    store = Store(tmp_path / "state" / "cardak" / "cardak.db")
    sid, since = seed(store, tmp_path / "projects.toml", ("stop-finished", {}))
    key = tmp_path / "config" / "cardak" / "anthropic_key"
    key.parent.mkdir(parents=True)
    key.write_text("sk-test")
    key.chmod(0o600)

    class Boom:
        def summarize(self, system: str, user: str) -> R:
            raise RuntimeError("boom")

    assert main([sid, repr(since)], lambda _key: Boom(), env) == 0
    assert capsys.readouterr() == ("", "")
    log = (tmp_path / "state" / "cardak" / "hook.log").read_text()
    assert "summarize:" in log and "boom" in log
    assert not store.has_summary(sid, since)


def test_main_writes_through_the_real_paths(tmp_path: Path) -> None:
    env = env_for(tmp_path)
    store = Store(tmp_path / "state" / "cardak" / "cardak.db")
    sid, since = seed(store, tmp_path / "projects.toml", ("stop-finished", {}))
    key = tmp_path / "config" / "cardak" / "anthropic_key"
    key.parent.mkdir(parents=True)
    key.write_text("sk-test")
    key.chmod(0o600)
    stub = Stub()
    assert main([sid, repr(since)], stub.make, env) == 0
    assert store.summaries_for([(sid, since)]) == {sid: "Did x; nothing asked"}


def test_main_with_a_broken_config_uses_defaults(tmp_path: Path) -> None:
    env = env_for(tmp_path)
    (tmp_path / "projects.toml").write_text("[[broken")
    assert main(["nope", "1.0"], Stub().make, env) == 0
