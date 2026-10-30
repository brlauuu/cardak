import json
from pathlib import Path
from types import TracebackType

import pytest

from cardak.collectors import prices
from cardak.collectors.prices import URL, Rates, fetch, parse, select

SUBSET = Path(__file__).parent / "fixtures" / "prices" / "litellm_subset.json"
CLAUDE = {
    "claude-opus-5-5",
    "claude-fable-5-1",
    "claude-sonnet-5-5",
    "claude-haiku-5-5",
    "claude-haiku-4-5",
}


FIREWORKS = "fireworks_ai/accounts/fireworks/models/llama-v3p3-70b-instruct"
OTHERS = {FIREWORKS, "gpt-4o-mini", "gpt-4o-transcribe-diarize"}


def test_select_keeps_anthropic_fireworks_and_openai() -> None:
    # no bedrock/… copies, no fireworks size classes ("fireworks-ai-default")
    assert set(select(SUBSET.read_bytes())) == CLAUDE | OTHERS


def test_parse_entries_without_cache_rates() -> None:
    found = parse(select(SUBSET.read_bytes()))
    assert found[FIREWORKS].base == Rates(9e-07, 9e-07, 0.0, 0.0, 0.0)
    assert found["gpt-4o-mini"].base.input == 1.5e-07
    assert found["gpt-4o-mini"].base.output == 6e-07


def test_parse_rates() -> None:
    found = parse(select(SUBSET.read_bytes()))
    assert set(found) == CLAUDE | OTHERS
    opus = found["claude-opus-5-5"]
    assert opus.base == Rates(4e-06, 2e-05, 5e-06, 8e-06, 2e-07)
    assert opus.above is None and opus.tier is None


def test_parse_tier() -> None:
    haiku = parse(select(SUBSET.read_bytes()))["claude-haiku-5-5"]
    assert haiku.above == 100_000
    assert haiku.tier == Rates(5e-07, 2.5e-06, 6.25e-07, 1e-06, 5e-08)


def test_parse_tier_falls_back_to_base_rate() -> None:
    entry = json.loads(SUBSET.read_text())["claude-haiku-5-5"]
    del entry["cache_creation_input_token_cost_above_1hr_above_100k_tokens"]
    tier = parse({"m": entry})["m"].tier
    assert tier is not None and tier.cache_write_1h == 2e-07


def test_parse_skips_incomplete() -> None:
    entries = select(SUBSET.read_bytes())
    missing = dict(entries["claude-opus-5-5"])
    del missing["cache_read_input_token_cost"]
    text = dict(entries["claude-opus-5-5"], input_cost_per_token="4e-06")
    found = parse(
        {"missing": missing, "text": text, "not-a-dict": 3, "ok": entries["claude-opus-5-5"]}
    )
    assert set(found) == {"ok"}


@pytest.mark.parametrize("raw", [b"<html>", b"[]", b'"x"'])
def test_select_rejects_garbage(raw: bytes) -> None:
    with pytest.raises(ValueError):
        select(raw)


class _Response:
    def __enter__(self) -> "_Response":
        return self

    def __exit__(
        self, t: type[BaseException] | None, e: BaseException | None, tb: TracebackType | None
    ) -> None:
        return None

    def read(self) -> bytes:
        return b"{}"


def test_fetch_reads_url(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, float]] = []

    def urlopen(url: str, timeout: float) -> _Response:
        calls.append((url, timeout))
        return _Response()

    monkeypatch.setattr(prices.urllib.request, "urlopen", urlopen)
    assert fetch() == b"{}"
    assert calls == [(URL, 10.0)]
