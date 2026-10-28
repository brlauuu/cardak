"""What a Claude Code reply would cost at API prices (#29).

Prices come from LiteLLM's public list, downloaded at most once a day by a running deck or
daemon (`PriceBook.refresh`) and cached; `BUILTIN` covers a first run with no network.
"""

import json
import os
import re
import tempfile
import time
from collections.abc import Callable, Mapping
from pathlib import Path

from cardak.collectors import prices
from cardak.collectors.prices import ModelPrice, Rates
from cardak.collectors.usage import Reply

MAX_AGE_S = 86_400.0
RETRY_S = 3_600.0  # after a failed download: offline must not mean a 10 s wait every pass
CACHE_NAME = "prices.json"

# LiteLLM's values on 2026-10-28, USD per token: the fallback until the first download
BUILTIN: dict[str, ModelPrice] = {
    "claude-opus-5-5": ModelPrice(Rates(4e-06, 2e-05, 5e-06, 8e-06, 2e-07)),
    "claude-fable-5-1": ModelPrice(Rates(1e-05, 5e-05, 1.25e-05, 2e-05, 2.5e-07)),
    "claude-sonnet-5-5": ModelPrice(Rates(2e-06, 1e-05, 2.5e-06, 4e-06, 1e-07)),
    "claude-haiku-5-5": ModelPrice(
        Rates(1e-07, 5e-07, 1.25e-07, 2e-07, 1e-08),
        100_000,
        Rates(5e-07, 2.5e-06, 6.25e-07, 1e-06, 5e-08),
    ),
    "claude-haiku-4-5": ModelPrice(Rates(1e-06, 5e-06, 1.25e-06, 2e-06, 1e-07)),
}

_SUFFIX = re.compile(r"\[[^\]]*\]$")  # claude-opus-5-5[1m]
_DATE = re.compile(r"-\d{8}$")  # claude-haiku-4-5-20251001


def lookup(table: Mapping[str, ModelPrice], model: str) -> ModelPrice | None:
    for name in (model, base := _SUFFIX.sub("", model), _DATE.sub("", base)):
        if name in table:
            return table[name]
    return None


def priced(r: Reply) -> bool:
    """False for fast mode, which the price list has no rate for."""
    return r.speed in (None, "standard")


def reply_cost(price: ModelPrice, r: Reply) -> float:
    context = r.input_tokens + r.cache_read_tokens + r.cache_write_5m_tokens
    context += r.cache_write_1h_tokens
    over = price.above is not None and price.tier is not None and context > price.above
    rates = price.tier if over and price.tier is not None else price.base
    return (
        r.input_tokens * rates.input
        + r.output_tokens * rates.output
        + r.cache_read_tokens * rates.cache_read
        + r.cache_write_5m_tokens * rates.cache_write_5m
        + r.cache_write_1h_tokens * rates.cache_write_1h
    )


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class PriceBook:
    """The current prices: the cached download over BUILTIN. Used from one thread."""

    def __init__(
        self,
        cache_file: Path,
        fetch: Callable[[], bytes] | None = None,  # None: prices.fetch, looked up when used
        clock: Callable[[], float] = time.time,
        log: Callable[[str], None] = lambda _m: None,
    ) -> None:
        self._file = cache_file
        self._fetch = fetch or (lambda: prices.fetch())
        self._clock = clock
        self._log = log
        self._failed_at: float | None = None
        self._loaded: tuple[float | None, Mapping[str, ModelPrice]] = (None, BUILTIN)

    def _mtime(self) -> float | None:
        try:
            return self._file.stat().st_mtime
        except OSError:
            return None

    def refresh(self) -> None:
        """Download the list if the cached copy is missing or a day old (not right after a
        failure); on any problem keep what is cached and log one line."""
        now, mtime = self._clock(), self._mtime()
        if mtime is not None and now - mtime < MAX_AGE_S:
            return
        if self._failed_at is not None and now - self._failed_at < RETRY_S:
            return
        try:
            entries = prices.select(self._fetch())
            if not prices.parse(entries):
                raise ValueError("no Claude prices in the download")
            _write_atomic(self._file, json.dumps(entries))
        except Exception as e:
            self._failed_at = now
            self._log(f"couldn't update prices: {e!r}")
            return
        self._failed_at = None

    def current(self) -> Mapping[str, ModelPrice]:
        mtime = self._mtime()
        if mtime != self._loaded[0]:
            self._loaded = (mtime, self._read())
        return self._loaded[1]

    def _read(self) -> Mapping[str, ModelPrice]:
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return BUILTIN
        return {**BUILTIN, **prices.parse(data)} if isinstance(data, dict) else BUILTIN
