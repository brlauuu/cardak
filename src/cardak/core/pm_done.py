"""The PM view's done column (#68): what got merged and closed in the last 7 days, in a few
bullets written by the summary provider chosen under `s` (#47–#49).

Only titles and numbers go out, never issue bodies or code. Each call is spend: it is recorded
in `pm_summaries` and counts toward `[summary] monthly_limit_usd` with the waiting summaries.
"""

import hashlib
import re
from pathlib import Path

from cardak.collectors.github import RepoData
from cardak.core.config import SummarySettings, key_file, summary_ready
from cardak.core.pm import NOTHING_DONE, Bullet, Done
from cardak.core.store import PmSummaryRow, Store
from cardak.core.summary import KeyProblem, ProviderTools, cost, month_start, provider_key

MAX_BULLETS = 6
MAX_ITEMS = 30  # the newest only: a CPU model read a busy week's 73 items for over 60 s (measured)
MAX_BULLET = 160
MAX_ERROR = 80
NOT_READY = "summaries are off: set one up with s"
CAPPED = "monthly summary cap reached"
UNUSABLE = "the summary couldn't be used"

DONE_SYSTEM_PROMPT = f"""\
You summarize a software project's last 7 days for its developer, who is choosing what to work
on next. You get one line per merged pull request or closed issue, with its number.

Reply with at most {MAX_BULLETS} lines, each starting with "- ": plain language, one change per
line, grouping related items, most important first. End each line with the numbers it covers in
parentheses, like (#203, #204). Use only numbers you were given. Nothing else: no heading, no
intro, no closing remark."""

_BULLET = re.compile(r"^\s*[-*•]\s+(.*\S)\s*$")
_REF = re.compile(r"#(\d+)")
_TRAILING_REFS = re.compile(r"\s*\((?:\s*#\d+\s*,?)+\)\s*\.?\s*$")


def _items(data: RepoData) -> list[tuple[float, int, str, str]]:
    """(time, number, url, line) for everything done, oldest first."""
    items = []
    for m in data.merged:
        closes = f" (closes {', '.join(f'#{n}' for n in m.closes)})" if m.closes else ""
        items.append((m.merged_at, m.number, m.url, f"PR #{m.number} merged: {m.title}{closes}"))
    for c in data.closed:
        items.append((c.closed_at, c.number, c.url, f"Issue #{c.number} closed: {c.title}"))
    return sorted(items)


def _sent(data: RepoData) -> list[tuple[float, int, str, str]]:
    return _items(data)[-MAX_ITEMS:]


def done_input(data: RepoData) -> str:
    items = _items(data)
    left_out = len(items) - MAX_ITEMS
    head = [f"({left_out} older items left out)"] if left_out > 0 else []
    return "\n".join(head + [line for *_, line in items[-MAX_ITEMS:]])


def done_hash(data: RepoData) -> str:
    return hashlib.sha256(done_input(data).encode()).hexdigest()


def parse_done(text: str, data: RepoData) -> tuple[Bullet, ...]:
    """The reply's bullets that cite at least one number they were given."""
    urls = {number: url for _, number, url, _ in _sent(data)}
    bullets: list[Bullet] = []
    for line in text.splitlines():
        found = _BULLET.match(line)
        if not found:
            continue
        refs = tuple(dict.fromkeys(n for n in map(int, _REF.findall(found.group(1))) if n in urls))
        if not refs:
            continue
        words = _TRAILING_REFS.sub("", found.group(1)).strip()
        bullets.append(Bullet(words[:MAX_BULLET].rstrip(), refs, urls[refs[0]]))
    return tuple(bullets[:MAX_BULLETS])


def fallback(data: RepoData, note: str | None) -> Done:
    """The merged PRs, newest first, then the issues closed without one: no LLM needed."""
    by_pr = {n for m in data.merged for n in m.closes}
    bullets = [
        Bullet(m.title, (m.number,), m.url) for m in sorted(data.merged, key=lambda m: -m.merged_at)
    ]
    bullets += [
        Bullet(c.title, (c.number,), c.url)
        for c in sorted(data.closed, key=lambda c: -c.closed_at)
        if c.number not in by_pr
    ]
    return Done(tuple(bullets), note) if bullets else Done((), NOTHING_DONE)


def _key(summary: SummarySettings, keys_dir: Path) -> str | None | Done:
    """The provider's key (None for one that needs none), or a Done whose note says why no
    summary can be made."""
    if not summary_ready(summary, keys_dir):
        return Done((), NOT_READY)
    try:
        return provider_key(summary.provider, keys_dir)
    except KeyProblem:
        return Done((), f"{NOT_READY} ({key_file(keys_dir, summary.provider).name} can't be used)")


def write_done(
    repo: str,
    data: RepoData,
    now: float,
    *,
    summary: SummarySettings,
    keys_dir: Path,
    tools: ProviderTools | None,
    store: Store,
) -> tuple[Done, bool]:
    """The done column, and whether it is settled: True when it may be kept for this input
    (a summary, or nothing to summarize), False when the next fetch should try again."""
    if not data.merged and not data.closed:
        return Done((), NOTHING_DONE), True
    if tools is None:
        return fallback(data, NOT_READY), False
    key = _key(summary, keys_dir)
    if isinstance(key, Done):
        return fallback(data, key.note), False
    if store.spend_since(month_start(now)) >= summary.monthly_limit_usd:
        return fallback(data, CAPPED), False
    name, settings = summary.provider, summary.active
    user, input_hash = done_input(data), done_hash(data)
    try:
        reply = tools.make(name, settings, key).summarize(DONE_SYSTEM_PROMPT, user)
    except Exception as e:  # any provider failure: the plain list, tried again next fetch
        message = " ".join(str(getattr(e, "message", e)).split())
        error = f"{type(e).__name__}: {message}"
        store.add_pm_summary(
            PmSummaryRow(repo, input_hash, settings.model or name, 0, 0, 0.0, now, error)
        )
        return fallback(data, f"the summary failed: {message[:MAX_ERROR]}"), False
    spent = cost(
        name, settings, reply.model, reply.input_tokens, reply.output_tokens, tools.prices()
    )
    store.add_pm_summary(
        PmSummaryRow(
            repo, input_hash, reply.model, reply.input_tokens, reply.output_tokens, spent, now
        )
    )
    bullets = parse_done(reply.text or "", data)
    if not bullets:
        return fallback(data, UNUSABLE), True
    return Done(bullets, None), True
