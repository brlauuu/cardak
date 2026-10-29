import os
import shutil
import threading
import time
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import pytest

from cardak.collectors import usage as usage_mod
from cardak.collectors.usage import Reply, Usage, read_usage
from cardak.core import spend
from cardak.core.prices import BUILTIN, PriceBook, lookup, reply_cost
from cardak.core.spend import MonthSpend, SpendCounter, month_spend
from cardak.core.store import Store
from cardak.core.summary import month_start

FIXTURES = Path(__file__).parent / "fixtures" / "transcripts" / "usage"
NOW = datetime(2026, 10, 28, 12, 0).timestamp()  # the fixtures are from 2026-10-20 and -21


def offline(tmp_path: Path) -> PriceBook:
    def fetch() -> bytes:
        raise OSError("offline")

    return PriceBook(tmp_path / "prices.json", fetch=fetch)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = tmp_path / "projects"
    shutil.copytree(FIXTURES, root / "-home-user-webshop")
    return root


def counter(tmp_path: Path, root: Path, db: str = "c.db") -> SpendCounter:
    return SpendCounter(Store(tmp_path / db), offline(tmp_path), root, clock=lambda: NOW)


def cost(r: Reply) -> float:
    price = lookup(BUILTIN, r.model)
    assert price is not None
    return reply_cost(price, r)


def all_replies(root: Path) -> dict[str, Reply]:
    found: dict[str, Reply] = {}
    for path in root.rglob("*.jsonl"):
        for r in read_usage(path, 0)[0].replies:
            found.setdefault(r.key, r)
    return found


def test_step_counts_each_reply_once(tmp_path: Path, root: Path) -> None:
    replies = all_replies(root)
    assert len(replies) == 4  # msg_u1 is in both session.jsonl and resumed.jsonl
    result = counter(tmp_path, root).step()
    assert result.total == pytest.approx(sum(map(cost, replies.values())))
    assert result.unpriced == ()
    assert result.since == month_start(NOW)


def test_latest_is_none_until_the_first_step(tmp_path: Path, root: Path) -> None:
    c = counter(tmp_path, root)
    assert c.latest is None
    assert c.step() == c.latest


def test_step_reads_only_appended(
    tmp_path: Path, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = counter(tmp_path, root)
    first = c.step().total
    reads: list[Path] = []
    real = usage_mod.read_usage
    monkeypatch.setattr(spend, "read_usage", lambda p, o, n: reads.append(p) or real(p, o, n))
    assert c.step().total == pytest.approx(first)
    assert reads == []
    session = root / "-home-user-webshop" / "session.jsonl"
    extra = (FIXTURES / "resumed.jsonl").read_text().splitlines()[2]
    with session.open("a") as f:
        f.write(extra.replace("msg_u3", "msg_new") + "\n")
    added = replace(read_usage(FIXTURES / "resumed.jsonl", 0)[0].replies[1], key="msg_new:req_u3")
    assert c.step().total == pytest.approx(first + cost(added))
    assert reads == [session]


def test_counter_rereads_replaced_file_without_doubling(tmp_path: Path, root: Path) -> None:
    c = counter(tmp_path, root)
    first = c.step().total
    session = root / "-home-user-webshop" / "session.jsonl"
    data = session.read_bytes()
    session.unlink()
    session.write_bytes(data)  # a new inode, same content
    assert c.step().total == pytest.approx(first)
    session.write_bytes(data[: len(data) // 2])  # shorter than the stored offset
    assert c.step().total == pytest.approx(first)


def test_two_counters_count_once(tmp_path: Path, root: Path) -> None:
    alone = counter(tmp_path, root, "alone.db").step().total
    deck, daemon = counter(tmp_path, root, "shared.db"), counter(tmp_path, root, "shared.db")
    assert deck.step().total == pytest.approx(alone)
    assert daemon.step().total == pytest.approx(alone)
    assert deck.step().total == pytest.approx(alone)


def test_step_writes_in_batches(
    tmp_path: Path, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(spend, "BATCH", 1)
    c = counter(tmp_path, root)
    calls: list[int] = []
    real = Store.record_usage
    monkeypatch.setattr(
        Store, "record_usage", lambda self, *a: calls.append(len(a[3].replies)) or real(self, *a)
    )
    total = c.step().total
    assert max(calls) == 1 and sum(calls) == 5  # 2 + 2 + 1 replies, one per transaction
    assert total == pytest.approx(counter(tmp_path, root, "other.db").step().total)


def test_month_spend_excludes_last_month() -> None:
    since = month_start(NOW)
    r = next(iter(all_replies(FIXTURES).values()))
    before = replace(r, key="old", ts=since - 1)
    assert month_spend([r, before], BUILTIN, since).total == pytest.approx(cost(r))


def test_month_spend_unpriced() -> None:
    r = next(iter(all_replies(FIXTURES).values()))
    unknown = replace(r, key="x", model="claude-x")
    fast = replace(r, key="f", speed="fast")
    result = month_spend([r, unknown, fast], BUILTIN, 0.0)
    assert result.total == pytest.approx(cost(r))
    assert result.unpriced == ("claude-opus-5-5 (fast mode)", "claude-x")


def test_by_cwd() -> None:
    r = next(iter(all_replies(FIXTURES).values()))
    rows = [r, replace(r, key="b", cwd="/home/user/notes"), replace(r, key="c", cwd=None)]
    result = month_spend(rows, BUILTIN, 0.0)
    assert set(result.by_cwd) == {"/home/user/webshop", "/home/user/notes", None}
    assert sum(result.by_cwd.values()) == pytest.approx(result.total)


def test_run_logs_and_continues(
    tmp_path: Path, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = counter(tmp_path, root)
    stop = threading.Event()
    steps: list[int] = []
    logged: list[str] = []

    def step() -> MonthSpend:
        steps.append(1)
        if len(steps) == 1:
            raise RuntimeError("boom")
        stop.set()
        return MonthSpend(0.0, 0.0, {}, ())

    monkeypatch.setattr(c, "step", step)
    monkeypatch.setattr(spend, "PASS_EVERY_S", 0.0)
    spend.run(c, stop, logged.append)
    assert len(steps) == 2
    assert logged == ["monthly spend: RuntimeError('boom')"]


def test_start_runs_in_a_daemon_thread(tmp_path: Path, root: Path) -> None:
    c = counter(tmp_path, root)
    stop = spend.start(c, lambda _m: None)
    try:
        thread = next(t for t in threading.enumerate() if t.name == "cardak-spend")
        assert thread.daemon
        deadline = time.monotonic() + 5
        while c.latest is None and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        stop.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert c.latest is not None


def test_files_before_this_month_are_not_read(tmp_path: Path, root: Path) -> None:
    for path in root.rglob("*.jsonl"):
        os.utime(path, (month_start(NOW) - 10, month_start(NOW) - 10))
    assert counter(tmp_path, root).step().total == 0.0


def test_run_survives_a_broken_log(
    tmp_path: Path, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = counter(tmp_path, root)
    stop = threading.Event()
    steps: list[int] = []

    def step() -> MonthSpend:
        steps.append(1)
        if len(steps) == 2:
            stop.set()
        raise RuntimeError("boom")

    def log(_message: str) -> None:
        raise OSError("log file unwritable")

    monkeypatch.setattr(c, "step", step)
    monkeypatch.setattr(spend, "PASS_EVERY_S", 0.0)
    spend.run(c, stop, log)
    assert len(steps) == 2


def test_step_reads_big_files_in_chunks(
    tmp_path: Path, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(spend, "READ_LIMIT", 700)
    sizes: list[int] = []
    real = usage_mod.read_usage

    def tracked(path: Path, offset: int, limit: int) -> tuple:
        usage, end = real(path, offset, limit)
        sizes.append(end - offset)
        return usage, end

    monkeypatch.setattr(spend, "read_usage", tracked)
    total = counter(tmp_path, root).step().total
    longest = max(
        len(line) + 1 for f in root.rglob("*.jsonl") for line in f.read_bytes().splitlines()
    )
    assert max(sizes) <= 700 + longest  # never a whole file: a chunk, read on to a line's end
    assert len(sizes) > 3  # session.jsonl alone took several reads
    assert total == pytest.approx(counter(tmp_path, root, "whole.db").step().total)


def test_run_counts_before_downloading_prices(
    tmp_path: Path, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # a download that hangs (DNS, slow network) must not hold up the count
    c = counter(tmp_path, root)
    stop = threading.Event()
    order: list[str] = []
    real_step = c.step

    def step() -> MonthSpend:
        order.append("step")
        return real_step()

    def refresh() -> None:
        order.append("refresh")
        stop.set()

    monkeypatch.setattr(c, "step", step)
    monkeypatch.setattr(c.prices, "refresh", refresh)
    spend.run(c, stop, lambda _m: None)
    assert order == ["step", "refresh"]


def test_step_builds_trends(tmp_path: Path, root: Path) -> None:
    trends = counter(tmp_path, root).step().trends
    assert trends is not None
    assert trends.today == date(2026, 10, 28)
    # Sep 29 is the window's first day: Oct 20 and 21 are days 21 and 22
    assert {k for k in trends.cells} == {("/home/user/webshop", 21), ("/home/user/webshop", 22)}


def test_trends_reach_back_into_last_month(tmp_path: Path) -> None:
    now = datetime(2026, 11, 3, 12, 0).timestamp()
    store = Store(tmp_path / "c.db")
    old = Reply("m:r", datetime(2026, 10, 25, 9).timestamp(), "s", "/w", "claude-opus-5-5",
                1, 1, 0, 0, 0, None)  # fmt: skip
    store.record_usage("/x.jsonl", 1, 1, Usage((old,), ()))
    empty = tmp_path / "none"
    empty.mkdir()
    result = SpendCounter(store, offline(tmp_path), empty, clock=lambda: now).step()
    assert result.total == 0
    assert (
        result.trends is not None and ("/w", 20) in result.trends.cells
    )  # the window starts Oct 5


def test_step_reuses_figures_when_nothing_changed(tmp_path: Path, root: Path) -> None:
    c = counter(tmp_path, root)
    first = c.step()
    assert c.step() is first


def test_step_sees_rows_another_counter_stored(tmp_path: Path, root: Path) -> None:
    deck = counter(tmp_path, root, "shared.db")
    before = deck.step()
    daemon = counter(tmp_path, root, "shared.db")
    late = Reply("m:late", NOW - 60, "s9", "/home/user/notes", "claude-opus-5-5",
                 1, 1, 0, 0, 0, None)  # fmt: skip
    daemon.store.record_usage("/y.jsonl", 1, 1, Usage((late,), ()))
    after = deck.step()  # nothing new in the transcripts for deck: the daemon stored it
    assert after is not before
    assert after.trends is not None and ("/home/user/notes", 29) in after.trends.cells


def test_step_rebuilds_after_midnight(tmp_path: Path, root: Path) -> None:
    now = [datetime(2026, 10, 27, 23, 59, 50).timestamp()]
    c = SpendCounter(Store(tmp_path / "c.db"), offline(tmp_path), root, clock=lambda: now[0])
    first = c.step()
    now[0] = datetime(2026, 10, 28, 0, 0, 20).timestamp()  # nothing new: only the date moved
    second = c.step()
    assert second is not first
    assert second.trends is not None and second.trends.today == date(2026, 10, 28)


def test_step_rebuilds_when_prices_change(tmp_path: Path, root: Path) -> None:
    c = counter(tmp_path, root)
    first = c.step()
    cheaper = {**BUILTIN, "claude-opus-5-5": BUILTIN["claude-haiku-5-5"]}
    c.prices._loaded = (None, cheaper)  # a download with new prices replaces the table
    second = c.step()
    assert second is not first and second.total < first.total


def store_unflagged(store: Store, root: Path) -> None:
    """Rows as a cardak before #86 stored them: every reply, no flags, offsets at the end."""
    for path in root.rglob("*.jsonl"):
        u, end = read_usage(path, 0)
        plain = tuple(replace(r, sidechain=False) for r in u.replies)
        store.record_usage(str(path), path.stat().st_ino, end, Usage(plain, u.prompts))


def test_backfill_flags_replies_stored_before_the_flag(tmp_path: Path, root: Path) -> None:
    c = counter(tmp_path, root)
    store_unflagged(c.store, root)
    stored = c.store.replies_since(0.0)
    before = month_spend(stored, BUILTIN, month_start(NOW))
    c.step()
    flagged = {r.key for r in c.store.replies_since(0.0) if r.sidechain}
    assert flagged and flagged == {k for k, r in all_replies(root).items() if r.sidechain}
    assert c.latest is not None and c.latest.total == before.total  # spend unchanged
    assert c.store.usage_mark() == (len(stored), c.store.usage_mark()[1])  # no reply added
    assert c.store.backfilled(spend.SIDECHAINS)


def test_backfill_runs_once(tmp_path: Path, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    c = counter(tmp_path, root)
    store_unflagged(c.store, root)
    c.step()
    calls: list[Path] = []
    real = spend.read_usage

    def counting(path: Path, offset: int, limit: int = usage_mod.READ_LIMIT) -> tuple[Usage, int]:
        calls.append(path)
        return real(path, offset, limit)

    monkeypatch.setattr(spend, "read_usage", counting)
    again = SpendCounter(c.store, offline(tmp_path), root, clock=lambda: NOW)
    again.step()
    assert calls == []  # done in the database: a new counter doesn't read it again


def test_backfill_on_an_empty_store_reads_nothing(
    tmp_path: Path, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = counter(tmp_path, root)
    flagged: list[list[str]] = []
    monkeypatch.setattr(c.store, "flag_sidechains", lambda keys: flagged.append(list(keys)))
    c.step()
    assert flagged == []  # nothing stored before: the normal pass flags as it stores
    assert c.store.backfilled(spend.SIDECHAINS)
    assert any(r.sidechain for r in c.store.replies_since(0.0))
