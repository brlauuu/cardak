"""This month's Claude Code usage at API prices (#29), counted in the background.

A `SpendCounter` runs in the deck and in `cardak daemon`, each on its own thread and store
connection. Every pass it reads what the transcripts gained since the last one, stores each
reply once (both may run at the same time: the store ignores a reply it already has), and
prices the month's replies by `cwd`. Mapping a `cwd` to a project is the snapshot's job.
"""

import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from cardak.collectors.prices import ModelPrice
from cardak.collectors.usage import (
    Reply,
    TranscriptFile,
    Usage,
    default_root,
    read_usage,
    transcript_files,
)
from cardak.core.prices import PriceBook, lookup, priced, reply_cost
from cardak.core.store import Store
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
        self._root = root or default_root()
        self._clock = clock
        self.latest: MonthSpend | None = None  # read from other threads; replaced whole

    def _read(self, f: TranscriptFile, stored: tuple[int, int] | None) -> None:
        inode, offset = stored or (f.inode, 0)
        if inode != f.inode or f.size < offset:  # replaced or cut: read it again from the top
            offset = 0
        if stored == (f.inode, f.size):
            return
        usage, end = read_usage(f.path, offset)
        path = str(f.path)
        chunks = _chunks(usage)
        for i, chunk in enumerate(chunks):
            # the offset moves with the last chunk only: a pass cut short reads the rest again
            done = end if i == len(chunks) - 1 else offset
            self.store.record_usage(path, f.inode, done, chunk)

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
            log(f"monthly spend: {e!r}")
        stop.wait(PASS_EVERY_S)


def start(counter: SpendCounter, log: Callable[[str], None]) -> threading.Event:
    stop = threading.Event()
    threading.Thread(
        target=run, args=(counter, stop, log), name="cardak-spend", daemon=True
    ).start()
    return stop
