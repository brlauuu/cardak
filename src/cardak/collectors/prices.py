"""Claude API prices from LiteLLM's public price list, the one network read cardak makes itself.

`select` keeps the Anthropic entries of the downloaded file (that subset is what gets cached),
`parse` turns them into rates. A community-maintained file, not an API: an entry without every
rate cardak needs is skipped rather than guessed.
"""

import json
import re
import urllib.request
from collections.abc import Mapping
from dataclasses import astuple, dataclass

URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
TIMEOUT = 10.0

_FIELDS = (  # Rates field order
    "input_cost_per_token",
    "output_cost_per_token",
    "cache_creation_input_token_cost",
    "cache_creation_input_token_cost_above_1hr",
    "cache_read_input_token_cost",
)
_TIER = re.compile(r"input_cost_per_token_above_(\d+)k_tokens")


@dataclass(frozen=True)
class Rates:
    """USD per token."""

    input: float
    output: float
    cache_write_5m: float
    cache_write_1h: float
    cache_read: float


@dataclass(frozen=True)
class ModelPrice:
    base: Rates
    above: int | None = None  # tokens in one request past which `tier` applies
    tier: Rates | None = None


def fetch(url: str = URL, timeout: float = TIMEOUT) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read()


def _wanted(name: str, entry: object) -> bool:
    if not isinstance(entry, dict):
        return False
    provider = entry.get("litellm_provider")
    if provider == "fireworks_ai":  # summaries (#48); "fireworks-ai-default" etc. are size classes
        return name.startswith("fireworks_ai/")
    return "/" not in name and provider in ("anthropic", "openai")


def select(raw: bytes) -> dict[str, dict[str, object]]:
    """The Anthropic, OpenAI and Fireworks APIs' own entries (no `bedrock/...` and the like).

    ValueError if not JSON.
    """
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("not a JSON object")
    return {k: v for k, v in data.items() if _wanted(k, v)}


def _rate(entry: Mapping[str, object], field: str) -> float | None:
    value = entry.get(field)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _rates(
    entry: Mapping[str, object], suffix: str = "", fallback: Rates | None = None
) -> Rates | None:
    """The five rates, each `<field><suffix>`; a missing one is fallback's, or the whole is None."""
    found = []
    for i, field in enumerate(_FIELDS):
        rate = _rate(entry, field + suffix)
        if rate is None and fallback is not None:
            rate = astuple(fallback)[i]
        if rate is None:
            return None
        found.append(rate)
    return Rates(*found)


def _price(entry: Mapping[str, object]) -> ModelPrice | None:
    base = _rates(entry)
    if base is None and entry.get("litellm_provider") != "anthropic":
        # Fireworks and OpenAI list no cache writes; summaries only use input and output
        found = _rate(entry, "input_cost_per_token"), _rate(entry, "output_cost_per_token")
        if found[0] is not None and found[1] is not None:
            return ModelPrice(Rates(found[0], found[1], 0.0, 0.0, 0.0))
    if base is None:
        return None
    tiers = sorted(int(m[1]) for k in entry if (m := _TIER.fullmatch(k)))
    if not tiers:
        return ModelPrice(base)
    # long context: e.g. input_cost_per_token_above_100k_tokens, and the same suffix on the rest
    return ModelPrice(base, tiers[0] * 1000, _rates(entry, f"_above_{tiers[0]}k_tokens", base))


def parse(entries: Mapping[str, object]) -> dict[str, ModelPrice]:
    found = {}
    for model, entry in entries.items():
        if isinstance(entry, Mapping) and (price := _price(entry)) is not None:
            found[model] = price
    return found
