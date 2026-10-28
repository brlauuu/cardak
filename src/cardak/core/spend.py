"""This month's Claude Code usage at API prices (#29), counted in the background.

A `SpendCounter` runs in the deck and in `cardak daemon`, each on its own thread and store
connection. Every pass it reads what the transcripts gained since the last one, stores each
reply once (both may run at the same time: the store ignores a reply it already has), and
prices the month's replies by `cwd`. Mapping a `cwd` to a project is the snapshot's job.
"""

import contextlib
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from cardak.collectors import usage as usage_mod
from cardak.collectors.prices import ModelPrice
from cardak.collectors.usage import (
    READ_LIMIT,
    Reply,
    TranscriptFile,
    Usage,
    read_usage,
    transcript_files,
)
from cardak.core.config import cache_dir
from cardak.core.prices import CACHE_NAME, PriceBook, lookup, priced, reply_cost
from cardak.core.store import Store, StoreError, db_path
from cardak.core.summary import month_start

PASS_EVERY_S = 30.0
BATCH = 500  # replies per transaction: a hook waiting on the write lock has 2 s


@dataclass(frozen=True)
class MonthSpend:
    since: float  # the month's start
    total: float  # USD, priced replies only
    by_cwd: Mapping[str | None, float]
    unpriced: tuple[str, ...]  # models with no price, "<model> (fast mode)" for fast mode


def month_spend(
    replies: Iterable[Reply], prices: Mapping[str, ModelPrice], since: float
) -> MonthSpend:
    by_cwd: dict[str | None, float] = {}
    unpriced: set[str] = set()
    for r in replies:
        if r.ts < since:
            continue
        price = lookup(prices, r.model)
        if price is None or not priced(r):
            unpriced.add(r.model if price is None else f"{r.model} (fast mode)")
            continue
        by_cwd[r.cwd] = by_cwd.get(r.cwd, 0.0) + reply_cost(price, r)
    return MonthSpend(since, sum(by_cwd.values()), by_cwd, tuple(sorted(unpriced)))


def _chunks(usage: Usage) -> list[Usage]:
    n = max(len(usage.replies), len(usage.prompts), 1)
    return [
        Usage(usage.replies[i : i + BATCH], usage.prompts[i : i + BATCH])
        for i in range(0, n, BATCH)
    ]


class SpendCounter:
    def __init__(
        self,
        store: Store,
        prices: PriceBook,
        root: Path | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store
        self.prices = prices
        self._root = root or usage_mod.default_root()
        self._clock = clock
        self.latest: MonthSpend | None = None  # read from other threads; replaced whole

    def _read(self, f: TranscriptFile, stored: tuple[int, int] | None) -> None:
        inode, offset = stored or (f.inode, 0)
        if inode != f.inode or f.size < offset:  # replaced or cut: read it again from the top
            offset = 0
        while offset < f.size:  # a few MB at a time: a first pass meets 80 MB transcripts
            usage, end = read_usage(f.path, offset, READ_LIMIT)
            if end == offset:
                return  # only a line still being written
            chunks = _chunks(usage)
            for i, chunk in enumerate(chunks):
                # the offset moves with the last batch only: a pass cut short reads it again
                done = end if i == len(chunks) - 1 else offset
                self.store.record_usage(str(f.path), f.inode, done, chunk)
            offset = end

    def step(self) -> MonthSpend:
        since = month_start(self._clock())
        stored = self.store.transcript_offsets()
        for f in transcript_files(self._root, since):
            self._read(f, stored.get(str(f.path)))
        self.latest = month_spend(self.store.replies_since(since), self.prices.current(), since)
        return self.latest


def run(counter: SpendCounter, stop: threading.Event, log: Callable[[str], None]) -> None:
    while not stop.is_set():
        try:
            counter.prices.refresh()
            counter.step()
        except Exception as e:  # a pass that fails is retried on the next one
            with contextlib.suppress(Exception):  # nor may a log that can't be written stop it
                log(f"monthly spend: {e!r}")
        stop.wait(PASS_EVERY_S)


def start(counter: SpendCounter, log: Callable[[str], None]) -> threading.Event:
    stop = threading.Event()
    threading.Thread(
        target=run, args=(counter, stop, log), name="cardak-spend", daemon=True
    ).start()
    return stop


def background(
    log: Callable[[str], None],
) -> tuple[Callable[[], MonthSpend | None], threading.Event] | None:
    """Start counting on a thread with its own store connection: the latest figure, and the
    event that stops it. None when the database can't be opened (logged)."""
    try:
        store = Store(db_path())
    except StoreError as e:
        log(f"monthly spend unavailable: {e}")
        return None
    counter = SpendCounter(store, PriceBook(cache_dir() / CACHE_NAME, log=log))
    return (lambda: counter.latest), start(counter, log)
