from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest

from cardak.collectors.prices import ModelPrice, Rates
from cardak.collectors.transcripts import read_transcript
from cardak.core.agents import SessionState
from cardak.core.config import ProviderSettings
from cardak.core.prices import BUILTIN
from cardak.core.summary import (
    TAGS,
    KeyProblem,
    build_input,
    cost,
    cost_note,
    key_warning,
    month_start,
    read_key,
    turn_text,
    validate,
    write_key,
)

FIXTURES = Path(__file__).parent / "fixtures" / "summaries"


def test_tags_cover_every_wait_kind() -> None:
    assert TAGS == {"permission": "approve", "question": "decide", "finished": "review"}


FW_MODEL = "accounts/fireworks/models/llama-v3p3-70b-instruct"
TABLE = {
    **BUILTIN,
    f"fireworks_ai/{FW_MODEL}": ModelPrice(Rates(9e-07, 9e-07, 0.0, 0.0, 0.0)),
    "gpt-4o-mini": ModelPrice(Rates(1.5e-07, 6e-07, 0.0, 0.0, 0.0)),
}
ANTHROPIC = ProviderSettings()
FIREWORKS = ProviderSettings("https://api.fireworks.ai/inference/v1", FW_MODEL)
OPENAI = ProviderSettings("https://api.openai.com/v1", "gpt-4o-mini")
LOCAL = ProviderSettings("http://127.0.0.1:8080/v1", "gpt-4o-mini")


def test_cost() -> None:
    assert cost("anthropic", ANTHROPIC, "claude-haiku-5-5", 1_000_000, 1_000_000, TABLE) == (
        pytest.approx(0.60)
    )
    assert cost("anthropic", ANTHROPIC, "claude-haiku-5-5", 1500, 30, TABLE) == pytest.approx(
        0.000165
    )


def test_cost_per_anthropic_model() -> None:
    assert cost("anthropic", ANTHROPIC, "claude-sonnet-5-5", 1_000_000, 0, TABLE) == (
        pytest.approx(2.0)
    )
    # an id the list doesn't know (a dated snapshot): charged as the model asked for
    assert cost("anthropic", ProviderSettings(model="claude-sonnet-5-5"), "claude-x", 10**6, 0,
                TABLE) == pytest.approx(2.0)  # fmt: skip
    assert cost("anthropic", ANTHROPIC, "claude-x", 10**6, 0, TABLE) == pytest.approx(0.10)


def test_cost_fireworks_adds_the_prefix() -> None:
    assert cost("fireworks", FIREWORKS, FW_MODEL, 1_000_000, 1_000_000, TABLE) == (
        pytest.approx(1.8)
    )


def test_cost_openai_only_on_openai() -> None:
    assert cost("openai", OPENAI, "gpt-4o-mini", 1_000_000, 0, TABLE) == pytest.approx(0.15)
    assert cost("openai", LOCAL, "gpt-4o-mini", 1_000_000, 0, TABLE) == 0.0
    assert cost("openai", OPENAI, "gpt-unknown", 1_000_000, 0, TABLE) == 0.0


def test_cost_local_and_plan_are_free() -> None:
    assert cost("ollama", ProviderSettings("http://h", "m"), "m", 10**6, 10**6, TABLE) == 0.0
    assert cost("codex", ProviderSettings(), "codex", 10**6, 10**6, TABLE) == 0.0


def test_cost_note() -> None:
    assert cost_note("ollama", ProviderSettings("http://h", "m"), TABLE) == "local, $0"
    assert cost_note("codex", ProviderSettings(), TABLE) == "your Codex plan, $0"
    assert cost_note("openai", LOCAL, TABLE) == (
        "custom server: no price, the monthly cap doesn't cover it"
    )
    unknown = ProviderSettings("https://api.openai.com/v1", "gpt-unknown")
    assert cost_note("openai", unknown, TABLE) == (
        "no price known: the monthly cap doesn't cover it"
    )
    assert cost_note("fireworks", FIREWORKS, TABLE) == "$0.90/$0.90 per M tokens"
    assert cost_note("anthropic", ANTHROPIC, TABLE) == "$0.10/$0.50 per M tokens"


def test_month_start_is_local_midnight_on_the_first() -> None:
    now = datetime(2026, 11, 1, 0, 0, 30).timestamp()
    assert month_start(now) == datetime(2026, 11, 1).timestamp()
    assert datetime(2026, 10, 31, 23, 59).timestamp() < month_start(now)
    assert month_start(datetime(2026, 10, 21, 15).timestamp()) == datetime(2026, 10, 1).timestamp()


@pytest.mark.parametrize("raw", ["sk-ant-x\n", "sk-ant-x\r\n", "  sk-ant-x  "])
def test_read_key_strips(tmp_path: Path, raw: str) -> None:
    key = tmp_path / "anthropic_key"
    key.write_text(raw)
    key.chmod(0o600)
    assert read_key(key) == "sk-ant-x"


def test_read_key_blank_or_absent_is_missing(tmp_path: Path) -> None:
    key = tmp_path / "anthropic_key"
    with pytest.raises(KeyProblem, match="missing"):
        read_key(key)
    key.write_text(" \n")
    key.chmod(0o600)
    with pytest.raises(KeyProblem, match="missing"):
        read_key(key)


def test_read_key_too_open(tmp_path: Path) -> None:
    key = tmp_path / "anthropic_key"
    key.write_text("k")
    key.chmod(0o640)
    with pytest.raises(KeyProblem, match="too open"):
        read_key(key)
    assert key_warning(key) == (
        "anthropic_key is readable by other users; run chmod 600 ~/.config/cardak/anthropic_key"
    )
    key.chmod(0o600)
    assert key_warning(key) is None
    assert key_warning(tmp_path / "absent") is None


def test_turn_text_is_only_the_current_turn() -> None:
    entries, _ = read_transcript(FIXTURES / "mid-turn.jsonl")
    text = turn_text(entries)
    assert "CURRENT TURN TEXT" in text
    assert "PREVIOUS TURN TEXT" not in text
    assert "hmm" not in text  # thinking is never sent


def test_turn_text_empty_without_assistant_text() -> None:
    entries, _ = read_transcript(FIXTURES / "no-assistant-text.jsonl")
    assert turn_text(entries) == ""


def test_build_input_contains_only_given_fields() -> None:
    state = SessionState(
        "s",
        kind="permission",
        last_prompt="p" * 3000,
        last_message="OLD TURN",
        turn_edits=2,
        turn_files=tuple(f"file{i}.py" for i in range(12)),
    )
    text = build_input(
        "api-gw",
        state,
        assistant_text="x" * 5000 + "THE ASK",
        tool=("Bash", '{"command": "cargo test"}'),
    )
    assert "OLD TURN" not in text  # the caller decides what the assistant text is
    assert "THE ASK" in text
    assert "x" * 4001 not in text
    assert "p" * 2001 not in text and "p" * 100 in text
    assert "cargo test" in text and "Bash" in text
    assert "file9.py" in text and "file10.py" not in text
    assert "api-gw" in text


def test_build_input_without_tool_or_text() -> None:
    text = build_input("notes", SessionState("s", kind="finished"), assistant_text="", tool=None)
    assert "notes" in text and "Bash" not in text


@dataclass
class R:
    text: str | None
    stop_reason: str | None = "end_turn"
    model: str = "claude-haiku-5-5"
    input_tokens: int = 1
    output_tokens: int = 1


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        (R("  Fixed it; wants tests  "), "Fixed it; wants tests"),
        (R('"Fixed it; wants tests"'), "Fixed it; wants tests"),
        (R("'Fixed it; wants tests'"), "Fixed it; wants tests"),
        (R("Fixed   it;\twants"), "Fixed it; wants"),
        (R(""), None),
        (R("   "), None),
        (R(None), None),
        (R("a\nb"), None),
        (R("x" * 121), None),
        (R("x" * 120), "x" * 120),
        (R("ok", stop_reason="max_tokens"), None),
        (R("ok", stop_reason="refusal"), None),
    ],
)
def test_validate(reply: R, expected: str | None) -> None:
    assert validate(reply) == expected


def test_write_key_is_private(tmp_path: Path) -> None:
    path = tmp_path / "fireworks_key"
    path.write_text("old")
    path.chmod(0o644)
    write_key(path, "  fw-123 \n")
    assert path.read_text() == "fw-123\n" and path.stat().st_mode & 0o777 == 0o600
    assert read_key(path) == "fw-123"


def test_write_key_makes_the_directory(tmp_path: Path) -> None:
    path = tmp_path / "cfg" / "openai_key"
    write_key(path, "k")
    assert path.stat().st_mode & 0o777 == 0o600
