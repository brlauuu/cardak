import os
from pathlib import Path

import pytest

from cardak.collectors.prices import parse, select
from cardak.collectors.usage import Reply
from cardak.core.config import cache_dir
from cardak.core.prices import BUILTIN, MAX_AGE_S, RETRY_S, PriceBook, lookup, priced, reply_cost

SUBSET = Path(__file__).parent / "fixtures" / "prices" / "litellm_subset.json"
M = 1_000_000


def reply(model: str = "claude-opus-5-5", speed: str | None = "standard", **tokens: int) -> Reply:
    counts = {"i": 0, "o": 0, "cr": 0, "w5": 0, "w1": 0} | tokens
    return Reply(
        "k", 0.0, "s", "/w", model, counts["i"], counts["o"], counts["cr"], counts["w5"],
        counts["w1"], speed,
    )  # fmt: skip


def test_builtin_matches_the_downloaded_list() -> None:
    assert parse(select(SUBSET.read_bytes())) == BUILTIN


def test_lookup_strips_date_and_suffix() -> None:
    assert lookup(BUILTIN, "claude-haiku-4-5-20251001") == BUILTIN["claude-haiku-4-5"]
    assert lookup(BUILTIN, "claude-opus-5-5[1m]") == BUILTIN["claude-opus-5-5"]
    assert lookup(BUILTIN, "claude-opus-5-5") == BUILTIN["claude-opus-5-5"]
    assert lookup(BUILTIN, "claude-x") is None


def test_reply_cost_flat() -> None:
    opus = BUILTIN["claude-opus-5-5"]
    assert reply_cost(opus, reply(i=M)) == pytest.approx(4.0)
    assert reply_cost(opus, reply(o=M)) == pytest.approx(20.0)
    assert reply_cost(opus, reply(cr=M)) == pytest.approx(0.2)
    assert reply_cost(opus, reply(w5=M)) == pytest.approx(5.0)
    assert reply_cost(opus, reply(w1=M)) == pytest.approx(8.0)


def test_reply_cost_tier() -> None:
    haiku = BUILTIN["claude-haiku-5-5"]
    # the whole request's input decides the tier: here 50k fresh + 100k read from cache
    assert reply_cost(haiku, reply("claude-haiku-5-5", i=50_000, cr=100_000)) == pytest.approx(
        50_000 * 5e-07 + 100_000 * 5e-08
    )
    assert reply_cost(haiku, reply("claude-haiku-5-5", i=90_000)) == pytest.approx(90_000 * 1e-07)


def test_priced_fast_mode() -> None:
    assert priced(reply(speed="standard")) and priced(reply(speed=None))
    assert not priced(reply(speed="fast"))


class Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_pricebook_downloads_when_stale(tmp_path: Path) -> None:
    calls: list[int] = []

    def fetch() -> bytes:
        calls.append(1)
        return SUBSET.read_bytes()

    cache = tmp_path / "cardak" / "prices.json"
    book = PriceBook(cache, fetch=fetch)
    book.refresh()
    assert cache.exists() and calls == [1]
    assert book.current()["claude-opus-5-5"] == BUILTIN["claude-opus-5-5"]
    book.refresh()  # the copy is fresh: no second download
    assert calls == [1]
    old = cache.stat().st_mtime - MAX_AGE_S - 1
    os.utime(cache, (old, old))
    book.refresh()
    assert calls == [1, 1]


def test_pricebook_reads_a_newer_price(tmp_path: Path) -> None:
    cache = tmp_path / "prices.json"
    raw = SUBSET.read_text().replace(
        '"input_cost_per_token": 4e-06', '"input_cost_per_token": 3e-06'
    )
    book = PriceBook(cache, fetch=lambda: raw.encode())
    book.refresh()
    assert book.current()["claude-opus-5-5"].base.input == 3e-06
    assert book.current()["claude-sonnet-5-5"] == BUILTIN["claude-sonnet-5-5"]


def test_pricebook_offline_uses_builtin_and_backs_off(tmp_path: Path) -> None:
    calls: list[int] = []
    logged: list[str] = []

    def fetch() -> bytes:
        calls.append(1)
        raise OSError("offline")

    clock = Clock(1000.0)
    book = PriceBook(tmp_path / "prices.json", fetch=fetch, clock=clock, log=logged.append)
    book.refresh()
    assert book.current() == BUILTIN
    assert len(logged) == 1 and "offline" in logged[0]
    clock.now += 30 * 60
    book.refresh()
    assert calls == [1]
    clock.now += RETRY_S
    book.refresh()
    assert calls == [1, 1]


def test_pricebook_bad_download_keeps_cache(tmp_path: Path) -> None:
    cache = tmp_path / "prices.json"
    PriceBook(cache, fetch=SUBSET.read_bytes).refresh()
    before = cache.read_text()
    old = cache.stat().st_mtime - MAX_AGE_S - 1
    os.utime(cache, (old, old))
    logged: list[str] = []
    for bad in (b"<html>", b"{}"):  # not JSON; JSON with no Claude prices in it
        PriceBook(cache, fetch=lambda bad=bad: bad, log=logged.append).refresh()
    assert cache.read_text() == before
    assert len(logged) == 2


def test_pricebook_unreadable_cache_is_builtin(tmp_path: Path) -> None:
    cache = tmp_path / "prices.json"
    cache.write_text("not json")
    assert PriceBook(cache, fetch=lambda: b"").current() == BUILTIN


def test_cache_dir_follows_xdg() -> None:
    assert cache_dir({"XDG_CACHE_HOME": "/c", "HOME": "/h"}) == Path("/c/cardak")
    assert cache_dir({"HOME": "/h"}) == Path("/h/.cache/cardak")
