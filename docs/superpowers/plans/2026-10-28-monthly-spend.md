# What this month would cost at API prices — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show what this month's Claude Code usage would cost at API prices, in total (deck header, phone page) and per project (project rows and cards).

**Architecture:** A background thread in the deck and in `cardak daemon` (a `SpendCounter`) reads what was appended to `~/.claude/projects/**/*.jsonl` since its last pass, stores one row per API reply in `cardak.db` (insert-or-ignore on `message.id:requestId`), and prices the month's rows with downloaded LiteLLM prices (built-in fallback) into a `MonthSpend` by `cwd`. `LiveSource` resolves those `cwd`s to projects with its resolver and puts the figures on the snapshot; `ui` and `web` format them.

**Tech Stack:** Python 3.13, stdlib `urllib.request` / `json` / `sqlite3` / `threading`, Textual, pytest.

**Spec:** `docs/superpowers/specs/2026-10-28-monthly-spend-design.md`

## Global Constraints

- Layering: `collectors` import nothing from cardak; `core` never imports `ui`, `web`, `wezterm`, `claude`, `llm`, `systemd`. All transcript parsing in `collectors/` (new `collectors/usage.py`); the price download in a collector (new `collectors/prices.py`).
- `cardak hook` must not import any new module: add `cardak.collectors.usage`, `cardak.collectors.prices`, `cardak.core.spend`, `cardak.core.prices`, `urllib.request` to `HOOK_FORBIDDEN` in `tests/test_cli.py`. `core/store.py` imports `collectors.usage` only under `TYPE_CHECKING` or inside functions.
- New tables only: `SCHEMA_VERSION = 5`, `MIN_READER` stays `1`. Reply rows pruned after 62 days (`SUMMARY_KEEP_SECONDS`).
- Writes in small transactions: at most `BATCH = 500` replies per commit.
- "This month" = `core.summary.month_start(now)` (local midnight on the 1st).
- Money: under $10 → `$0.42`; else whole dollars with separators (`$37`, `$1,204`); `+` after the number when some replies are unpriced.
- Header part copy, exact: ` · $412 this month at API prices` (or `$412+`). Project row: `$37 this month` before the RAM numbers. No spend → nothing shown (not `$0`) on rows; header shows `$0 this month at API prices` once the first pass has finished and found nothing; no header part before that.
- Price download: `https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json`, at most once per 24 h per cached copy, only from a running deck or daemon, timeout 10 s, a failed attempt retried after 1 h; cache `<XDG_CACHE_HOME or ~/.cache>/cardak/prices.json`, written atomically. GET only, no headers beyond urllib's defaults.
- Tests never touch the real `~/.claude`, `~/.cache` or the network: `tests/conftest.py` also points `XDG_CACHE_HOME` at a temp dir; the transcripts root and the fetch function are injected.
- Pass interval: `PASS_EVERY_S = 30.0` in both deck and daemon.

## Measured facts (2026-10-28, this machine, Claude Code 2.1.3xx)

- 21,155 usage lines → 10,018 unique `(message.id, requestId)` this month: each content block repeats the usage.
- Models seen: `claude-opus-5-5`, `claude-fable-5-1`, `claude-sonnet-5-5`, `claude-haiku-5-5`, `claude-haiku-4-5-20251001`, `<synthetic>` (skip).
- `usage.speed` is `"standard"` or absent; `"fast"` (fast mode) has no Anthropic entry in LiteLLM → such a reply is unpriced.
- `usage.iterations` is one `{"type": "message", ...}` entry equal to the top-level counts; `advisorModel` carries no separate tokens. Price the top-level counts as `message.model`.
- Half of the replies have more than 200k input tokens in total. LiteLLM gives a long-context tier only for `claude-haiku-5-5` (`*_above_100k_tokens`); others are flat to 1M.
- LiteLLM Anthropic entries: key without `/` and `litellm_provider == "anthropic"`; fields `input_cost_per_token`, `output_cost_per_token`, `cache_creation_input_token_cost` (5 min), `cache_creation_input_token_cost_above_1hr`, `cache_read_input_token_cost`, optional `<field>_above_<N>k_tokens` tiers.
- A full first pass over this month (~480 MB, 189 files, lines pre-filtered on `b'"usage"'`) takes ~3 s of CPU.
- Transcripts sit at depths 1–3 under `~/.claude/projects` (subagents below the session dir).

## Review Focus

1. A transcript line cut in half (Claude Code mid-write): the pass stops at the last `\n`, and the next pass reads the rest of the line whole — test `test_read_replies_stops_at_partial_line`.
2. A transcript replaced or truncated (same path, smaller size or new inode): read again from 0 without double counting — test `test_counter_rereads_replaced_file_without_doubling`.
3. Deck and daemon counting at once: two counters on two connections to one db give the same total as one — test `test_two_counters_count_once`.
4. Offline first run (no cache, fetch fails): built-in prices are used, one log line, no retry for an hour — test `test_pricebook_offline_uses_builtin_and_backs_off`.
5. The 1st of the month: replies from the previous month are not in the figure — test `test_month_spend_excludes_last_month`.

---

### Task 1: Transcript usage collector

**Files:**
- Create: `src/cardak/collectors/usage.py`
- Create: `tests/fixtures/transcripts/usage/` — `session.jsonl` (two replies, the first as 3 content-block lines repeating id/usage; one `<synthetic>`; one user line; one `cache_creation` breakdown absent; prompts: one plain string prompt, one with text blocks, one `isMeta`, one tool-result-only user line), `resumed.jsonl` (repeats one reply of `session.jsonl`), `sub/agent-x.jsonl` (one `isSidechain: true` reply). Build them from `basic.jsonl`'s structure with neutral text and made-up paths (`/home/user/webshop`).
- Test: `tests/test_usage.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Reply: key: str; ts: float; session_id: str | None; cwd: str | None; model: str; input_tokens: int; output_tokens: int; cache_read_tokens: int; cache_write_5m_tokens: int; cache_write_1h_tokens: int; speed: str | None` (field order = table column order).
  - `@dataclass(frozen=True) class Prompt: key: str; ts: float; session_id: str | None; cwd: str | None; chars: int` (key = the line's `uuid`; chars = total length of its text blocks or string content)
  - `@dataclass(frozen=True) class Usage: replies: tuple[Reply, ...] = (); prompts: tuple[Prompt, ...] = ()`
  - `@dataclass(frozen=True) class TranscriptFile: path: Path; size: int; inode: int; mtime: float`
  - `def transcript_files(root: Path, since: float) -> list[TranscriptFile]` — every `*.jsonl` under `root` at any depth with `mtime >= since`; missing root → `[]`.
  - `def read_usage(path: Path, offset: int) -> tuple[Usage, int]` — replies and prompts in bytes `[offset, last "\n"]`, and the offset after that newline; `OSError` → `(Usage(), offset)`.
  - `def default_root() -> Path` → `Path.home() / ".claude" / "projects"`.

- [ ] **Step 1: Write the failing tests** in `tests/test_usage.py`:
  - `test_read_replies_one_per_reply`: `session.jsonl` from 0 → 2 replies; keys `"<message.id>:<requestId>"`; the first reply's counts equal the fixture's usage; `ts` is the ISO `timestamp` as epoch seconds; `cwd == "/home/user/webshop"`; offset == file size.
  - `test_read_replies_splits_cache_writes`: `cache_write_5m_tokens`/`_1h_` from `usage.cache_creation.ephemeral_5m/1h_input_tokens`; without the breakdown, all of `cache_creation_input_tokens` is 5 m.
  - `test_read_replies_skips_synthetic_and_garbage`: `<synthetic>`, non-JSON lines, lines with no `message.id` → not returned, no exception.
  - `test_read_replies_stops_at_partial_line`: write the file's bytes minus the last 10 → offset is the start of the last line; append the rest → second call from that offset returns that reply once.
  - `test_read_replies_keeps_sidechain`: `sub/agent-x.jsonl` → 1 reply, 0 prompts (its `isSidechain` user line is Claude prompting the subagent).
  - `test_read_usage_prompts`: `session.jsonl` → exactly the 2 typed prompts, `chars` equal to their text lengths, `session_id` and `cwd` set; `isMeta` and tool-result-only lines are not prompts.
  - every reply carries `session_id` from the record's `sessionId`.
  - `test_transcript_files_any_depth_and_since`: all three fixtures found (copied into `tmp_path`); one with `os.utime` before `since` left out; missing root → `[]`.
- [ ] **Step 2:** `uv run pytest tests/test_usage.py -v` → FAIL (module missing).
- [ ] **Step 3: Implement** `collectors/usage.py`. Read bytes with `open(path, "rb")`, `seek(offset)`; end = `rfind(b"\n") + 1`; skip lines containing neither `b'"usage"'` nor `b'"user"'` before `json.loads`; replies from `type == "assistant"` records with a dict `message.usage`; prompts from `type == "user"` records that are not `isMeta`/`isSidechain` and have text (string content or `text` blocks); ints via a helper that turns anything non-int into 0. Module docstring in the style of `transcripts.py` (not a stable API; anything unexpected is skipped).
- [ ] **Step 4:** tests PASS; `uv run ruff check src tests && uv run pyright src/cardak/collectors/usage.py`.
- [ ] **Step 5: Commit** `feat(collectors): read per-reply token usage and prompts from transcripts (#29)`.

### Task 2: Price list collector

**Files:**
- Create: `src/cardak/collectors/prices.py`
- Create: `tests/fixtures/prices/litellm_subset.json` — the 5 measured Anthropic models copied from the downloaded file (keep their real fields; `claude-haiku-5-5` with its `_above_100k_tokens` fields), plus one `bedrock/...` entry and one `gpt-x` entry that must be dropped, plus `"sample_spec"`.
- Test: `tests/test_prices_collector.py`

**Interfaces:**
- Produces:
  - `URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"`
  - `@dataclass(frozen=True) class Rates: input: float; output: float; cache_write_5m: float; cache_write_1h: float; cache_read: float` (USD per token)
  - `@dataclass(frozen=True) class ModelPrice: base: Rates; above: int | None = None; tier: Rates | None = None` (`above` in tokens, e.g. `100_000`)
  - `def fetch(url: str = URL, timeout: float = 10.0) -> bytes` (`urllib.request.urlopen`; raises `OSError`/`ValueError` on failure)
  - `def select(raw: bytes) -> dict[str, dict[str, object]]` — the Anthropic entries (key has no `/`, `litellm_provider == "anthropic"`); `ValueError` for non-JSON or a non-object top level.
  - `def parse(entries: Mapping[str, object]) -> dict[str, ModelPrice]` — models with all five base fields as numbers; others skipped. Tier: the first `input_cost_per_token_above_<N>k_tokens` sets `above = N * 1000`; each tier rate from `<base field>_above_<N>k_tokens` (1 h write: `cache_creation_input_token_cost_above_1hr_above_<N>k_tokens`), falling back to the base rate when missing.

- [ ] **Step 1: Failing tests:** `test_select_keeps_anthropic_only` (5 keys); `test_parse_rates` (`claude-opus-5-5` base == `Rates(4e-06, 2e-05, 5e-06, 8e-06, 2e-07)`, no tier); `test_parse_tier` (`claude-haiku-5-5`: `above == 100_000`, `tier.input == 5e-07`, `tier.cache_write_1h == 1e-06`); `test_parse_skips_incomplete` (an entry missing `cache_read_input_token_cost` or with a string rate is absent); `test_select_rejects_garbage` (`b"<html>"`, `b"[]"` → `ValueError`); `test_fetch_reads_url` (monkeypatch `urllib.request.urlopen` with a fake context manager; asserts URL and timeout passed).
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** PASS, ruff, pyright.
- [ ] **Step 5: Commit** `feat(collectors): parse Claude prices from LiteLLM's price list (#29)`.

### Task 3: Store tables for replies, prompts and transcript offsets

**Files:**
- Modify: `src/cardak/core/store.py` (schema, `SCHEMA_VERSION = 5`, new methods, `prune`)
- Modify: `tests/test_cli.py` (`HOOK_FORBIDDEN` += the five modules in Global Constraints)
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `Reply` (Task 1).
- Produces (all raise `StoreError` on `sqlite3.Error`):
  - Tables: `claude_replies (reply_key TEXT PRIMARY KEY, ts REAL NOT NULL, session_id TEXT, cwd TEXT, model TEXT NOT NULL, input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL, cache_read_tokens INTEGER NOT NULL, cache_write_5m_tokens INTEGER NOT NULL, cache_write_1h_tokens INTEGER NOT NULL, speed TEXT)` + index on `ts`; `claude_prompts (prompt_key TEXT PRIMARY KEY, ts REAL NOT NULL, session_id TEXT, cwd TEXT, chars INTEGER NOT NULL)` + index on `ts` (read by #88; nothing reads it in #29 except tests); `transcript_offsets (path TEXT PRIMARY KEY, inode INTEGER NOT NULL, offset INTEGER NOT NULL)`.
  - `def record_usage(self, path: str, inode: int, offset: int, usage: Usage) -> int` — one transaction: `INSERT OR IGNORE` the replies and prompts, upsert the offset; returns how many reply rows were new.
  - `def prompts_since(self, ts: float) -> list[Prompt]` — oldest first.
  - `def transcript_offsets(self) -> dict[str, tuple[int, int]]` — path → (inode, offset).
  - `def replies_since(self, ts: float) -> list[Reply]` — oldest first.
  - `prune`: `DELETE FROM claude_replies WHERE ts < now - SUMMARY_KEEP_SECONDS`.

- [ ] **Step 1: Failing tests:** `test_record_usage_ignores_duplicates` (same reply and prompt twice across two calls → second returns 0; `replies_since(0)` / `prompts_since(0)` have each once); `test_record_usage_moves_offset` (offsets read back); `test_replies_since_filters` ; `test_prune_drops_old_replies` (replies and prompts: 63 days old gone, 61 kept); `test_schema_5_opens_in_place` (a v4 file from the existing fixture/helper upgrades to 5 with `application_id` still 1, existing rows kept). Hook guard tests in `test_cli.py` keep passing with the longer `HOOK_FORBIDDEN`.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3: Implement** (transaction like `record_minutes`). Import `Reply`/`Prompt`/`Usage` under `TYPE_CHECKING`; build rows in `replies_since`/`prompts_since` with a function-local import.
- [ ] **Step 4:** `uv run pytest tests/test_store.py tests/test_cli.py -v` PASS.
- [ ] **Step 5: Commit** `feat(core): store Claude Code replies, prompts and transcript offsets (#29)`.

### Task 4: Prices in core: built-in table, lookup, cost, the daily download

**Files:**
- Create: `src/cardak/core/prices.py`
- Modify: `src/cardak/core/config.py` (add `cache_dir(env: Mapping[str, str] | None = None) -> Path`, same rules as `wezterm/schemes.default_cache_dir`)
- Modify: `tests/conftest.py` (also `XDG_CACHE_HOME` → temp dir; update the docstring), `tests/test_isolation.py` (assert it)
- Test: `tests/test_prices.py`

**Interfaces:**
- Consumes: `Rates`, `ModelPrice`, `fetch`, `select`, `parse` (Task 2); `Reply` (Task 1).
- Produces:
  - `BUILTIN: dict[str, ModelPrice]` — the five measured models with the LiteLLM values of 2026-10-28 (comment saying so). `claude-haiku-4-5` under both its base and dated id is not needed: lookup strips the date.
  - `def lookup(prices: Mapping[str, ModelPrice], model: str) -> ModelPrice | None` — exact; else without a trailing `[...]`; else without a trailing `-\d{8}`; else `None`.
  - `def reply_cost(price: ModelPrice, r: Reply) -> float` — uses `price.tier` when `price.above` is set and `input + cache_read + cache_write_5m + cache_write_1h > price.above`.
  - `def priced(r: Reply) -> bool` — `False` when `r.speed` is set and not `"standard"`.
  - `MAX_AGE_S = 86_400.0`, `RETRY_S = 3_600.0`, `CACHE_NAME = "prices.json"`
  - `class PriceBook: __init__(self, cache_file: Path, fetch: Callable[[], bytes] = prices.fetch, clock: Callable[[], float] = time.time, log: Callable[[str], None] = lambda _m: None)`; `def refresh(self) -> None` (fetch if the cache file is missing or older than `MAX_AGE_S` and no failure in the last `RETRY_S`; on success write `json.dumps(select(raw))` atomically via a temp file + `os.replace` in the same dir; on any `Exception` keep the cache and `log("couldn't update prices: <e!r>")`); `def current(self) -> Mapping[str, ModelPrice]` (`{**BUILTIN, **parse(cached)}`; re-read only when the cache file's mtime changed; unreadable cache → `BUILTIN`).

- [ ] **Step 1: Failing tests:** `test_lookup_strips_date_and_suffix` (`claude-haiku-4-5-20251001` → haiku-4-5 entry; `claude-opus-5-5[1m]` → opus; `claude-x` → None); `test_reply_cost_flat` (opus: 1M input → `4.0`; 1M cache read → `0.2`; 1M 1 h write → `8.0`); `test_reply_cost_tier` (haiku-5-5 at 150k input → tier input rate; at 90k → base); `test_priced_fast_mode`; `test_pricebook_downloads_when_stale` (fake fetch returns the Task 2 fixture bytes; file written; `current()["claude-opus-5-5"]` from it; a second `refresh()` within a day does not call fetch); `test_pricebook_offline_uses_builtin_and_backs_off` (fetch raises `OSError`; `current() == BUILTIN`; one log line; `refresh()` again 30 min later doesn't call fetch, 61 min later does); `test_pricebook_bad_download_keeps_cache` (cache present, fetch returns `b"<html>"` → cache content unchanged); `test_cache_dir_follows_xdg`.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** PASS, ruff, pyright.
- [ ] **Step 5: Commit** `feat(core): price replies with daily downloaded prices (#29)`.

### Task 5: The counter: incremental passes and the month's figure

**Files:**
- Create: `src/cardak/core/spend.py`
- Test: `tests/test_spend.py`

**Interfaces:**
- Consumes: Tasks 1, 3, 4.
- Produces:
  - `@dataclass(frozen=True) class MonthSpend: since: float; total: float; by_cwd: Mapping[str | None, float]; unpriced: tuple[str, ...]` (sorted model names, fast mode as `"<model> (fast mode)"`)
  - `def month_spend(replies: Iterable[Reply], prices: Mapping[str, ModelPrice], since: float) -> MonthSpend` — replies with `ts >= since` only.
  - `PASS_EVERY_S = 30.0`; `BATCH = 500`
  - `class SpendCounter: __init__(self, store: Store, prices: PriceBook, root: Path | None = None, clock: Callable[[], float] = time.time)`; `def step(self) -> MonthSpend` (for each `transcript_files(root, month_start(now))` whose `(inode, size)` differs from the stored offset: start at 0 when the inode changed or size < offset; `read_usage` from the offset; `record_usage` in chunks of `BATCH` replies (prompts split alongside) with the final offset on the last chunk; then `month_spend(store.replies_since(since), prices.current(), since)`; stores the result in `latest`); `latest: MonthSpend | None` (attribute; `None` until the first `step` returns).
  - `def run(counter: SpendCounter, stop: threading.Event, log: Callable[[str], None]) -> None` — loop: `counter.prices.refresh()`, `counter.step()`, `stop.wait(PASS_EVERY_S)`; any `Exception` logged (`log(f"monthly spend: {e!r}")`) and the loop goes on.
  - `def start(counter: SpendCounter, log: Callable[[str], None]) -> threading.Event` — a `daemon=True` thread named `cardak-spend` running `run`; returns its stop event.

- [ ] **Step 1: Failing tests** (fixtures copied into `tmp_path`, a real `Store` in `tmp_path`, a `PriceBook` whose fetch raises so `BUILTIN` applies):
  - `test_step_counts_each_reply_once` (session + resumed + sub → 3 replies; `total` equals the sum of `reply_cost` of those 3).
  - `test_step_reads_only_appended` (second `step` with no change does not call `read_usage` — monkeypatch to count; after appending one reply line, total grows by its cost).
  - `test_counter_rereads_replaced_file_without_doubling` (rewrite `session.jsonl` with the same content under a new inode → total unchanged).
  - `test_two_counters_count_once` (two `Store`s on one db file, each its own `SpendCounter`, both `step` → equal totals, equal to one counter's).
  - `test_month_spend_excludes_last_month` (a reply dated the previous month is left out; `since == month_start(clock())`).
  - `test_month_spend_unpriced` (model `claude-x` and a `speed: "fast"` reply → not in `total`, named in `unpriced`).
  - `test_by_cwd` (two cwds and one `None` → three entries summing to `total`).
  - `test_run_logs_and_continues` (a `step` that raises once → one log line, next iteration runs; stop after two).
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** PASS, ruff, pyright.
- [ ] **Step 5: Commit** `feat(core): count this month's Claude Code spend in the background (#29)`.

### Task 6: Spend on the snapshot

**Files:**
- Modify: `src/cardak/core/snapshot.py` (`Group.spend_usd: float | None = None`; `DeckSnapshot.month_spend: float | None = None`, `DeckSnapshot.spend_unpriced: tuple[str, ...] = ()`)
- Modify: `src/cardak/core/source.py` (`LiveSource.__init__(..., spend: Callable[[], MonthSpend | None] = lambda: None)`; `_with_spend(snap) -> DeckSnapshot` applied at the end of `_poll`, both the normal and the WezTerm-unavailable return; the unpriced warning in `_warnings_now`)
- Test: `tests/test_source.py`

**Interfaces:**
- Consumes: `MonthSpend` (Task 5).
- Produces: per-group figure = sum of `by_cwd` entries whose `cwd` resolves (`self._resolver.resolve(Path(cwd))`, `None` cwd → `resolve(None)`) to the group's `(kind, key)`; the `waiting` group never gets one; groups with no matching cwd keep `None`. The `cwd → (kind, key)` sums are cached per `(MonthSpend object, resolver object)` so a poll does no resolving when neither changed. Warning text, exact: `no API price for <models joined by ", ">; left out of this month's figure`.

- [ ] **Step 1: Failing tests** (existing `LiveSource` test helpers with fake panes/resolver): `test_spend_by_project` (cwd under a configured project and one under a git root → each group's `spend_usd`; header total = `MonthSpend.total`, including a cwd with no pane open); `test_spend_none_before_first_pass` (`month_spend is None`, groups `None`); `test_spend_never_on_waiting_group`; `test_spend_follows_config_change` (a project added later takes its cwd's figure on the next poll); `test_spend_with_wezterm_down` (`month_spend` still set); `test_unpriced_warning`.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** `uv run pytest tests/test_source.py tests/test_snapshot.py -v` PASS.
- [ ] **Step 5: Commit** `feat(core): put the month's spend on the snapshot (#29)`.

### Task 7: The deck shows it

**Files:**
- Modify: `src/cardak/ui/widgets.py` (`def fmt_usd(usd: float) -> str`; `group_label` inserts `f"{fmt_usd(g.spend_usd)} this month  "` before the usage text when `g.spend_usd`, style `"dim"`)
- Modify: `src/cardak/ui/app.py` (`_render`: after the waiting part, `if raw.month_spend is not None: sub_title += f" · {fmt_usd(raw.month_spend)}{'+' if raw.spend_unpriced else ''} this month at API prices"`, in both the query and no-query cases)
- Test: `tests/test_widgets.py` (or where `group_label` is tested now), `tests/test_app.py`

- [ ] **Step 1: Failing tests:** `test_fmt_usd` (`0.421 → "$0.42"`, `9.994 → "$9.99"`, `37.4 → "$37"`, `1204.6 → "$1,205"`, `0.0 → "$0"`); `test_group_label_spend` (plain text contains `"webshop  ~/webshop  $37 this month  RAM"`; `spend_usd=None` and `0.0` → no `"this month"`); `test_header_spend` (app with a snapshot `month_spend=412.0` → `sub_title.endswith(" · $412 this month at API prices")`; with `spend_unpriced` → `"$412+"`; `None` → no `"this month"`).
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3: Implement.** `fmt_usd(0.0)` is `"$0"` (spec copy `$0 this month at API prices`); every other value under $10 keeps cents.
- [ ] **Step 4:** PASS.
- [ ] **Step 5: Commit** `feat(ui): show this month's spend at API prices in the deck (#29)`.

### Task 8: The phone page shows it

**Files:**
- Modify: `src/cardak/web/view.py` (`snapshot_json` += `"month_spend": snap.month_spend, "spend_unpriced": bool(snap.spend_unpriced)`; `_project` += `"spend_usd": g.spend_usd`)
- Modify: `src/cardak/web/page.html` (a `usd(v)` formatter matching `fmt_usd`; the total as a text node after `#machine` in the top bar, e.g. a `<span id="spend">`, set via `textContent`, hidden when `month_spend` is null; on each `projectCard` a muted `"$37 this month"` span when `spend_usd` is truthy). If an inline `<script>`/`<style>` changes, update its CSP hash the way `web/server.py` computes it (the existing test checks it).
- Test: `tests/test_web_view.py` (or the existing view test file), `tests/test_web_server.py` CSP test stays green

- [ ] **Step 1: Failing tests:** `test_snapshot_json_spend` (total, unpriced flag, per-project `spend_usd`; `None` passes through as `None`); `test_snapshot_json_whitelist` keeps passing with the new keys added to its expected set.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** PASS; then check by hand in headless Chrome at 390×844 against a scratch daemon, as in M7 (scratch `XDG_*`, a scratch transcripts root via the daemon's wiring from Task 9).
- [ ] **Step 5: Commit** `feat(web): show this month's spend on the phone page (#29)`.

### Task 9: Run the counter in the deck and the daemon

**Files:**
- Modify: `src/cardak/cli.py` (`_deck`: open a third `Store(db_path())` for the counter, `counter = SpendCounter(store, PriceBook(cache_dir() / CACHE_NAME, log=log))`, `stop = spend.start(counter, log)` with `log = lambda m: hook.log_problem(os.environ, m)`; pass `spend=lambda: counter.latest` to `LiveSource`; after `.run()`, `stop.set()` and close the store. Store failure → no counter, `spend` stays default)
- Modify: `src/cardak/core/daemon.py` (`main`: same with its own `Store` and `log=log`; `build_source(config_file, agents, spend)` passes it to both `LiveSource` branches; `stop.set()` on exit)
- Test: `tests/test_daemon.py`, `tests/test_cli.py`

- [ ] **Step 1: Failing tests:** `test_build_source_passes_spend` (both branches; `source.poll()` with a fake `spend` returning a `MonthSpend` carries `month_spend`); `test_deck_starts_counter` (monkeypatch `DeckApp.run` to a no-op and `spend.start` to record its call → called once and its event set after run); `test_hook_imports_only_what_it_needs` still green.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3: Implement.** Keep the imports inside the functions (cli rule: every command imports what it needs inside its function).
- [ ] **Step 4:** `uv run pytest -q` PASS; `python -X importtime -c "import cardak.cli" 2>&1 | grep -E "spend|prices|usage"` → nothing.
- [ ] **Step 5: Live check** (isolated, like M7): scratch `XDG_STATE_HOME`/`XDG_CONFIG_HOME`/`XDG_CACHE_HOME`, `CARDAK_CONFIG`; run `cardak daemon` with the web on and a copy of a few real transcripts as root is not possible from the CLI, so check against the real `~/.claude/projects` read-only (it only reads): the header and phone page show a figure; compare it to an independent sum (a short script over this month's transcripts with the same dedupe and prices). Measure the daemon's CPU over 5 minutes after the first pass (`ps -o time`), stays near M5's 0.8%.
- [ ] **Step 6: Commit** `feat: count this month's spend in the deck and the daemon (#29)`.

### Task 10: Docs, changelog, architecture check

**Files:**
- Modify: `AGENTS.md` — Layout (`collectors/{usage, prices}.py`, `core/{prices, spend}.py`), files table (`cardak.db` row += `claude_replies`, `claude_prompts` (stored for #88), `transcript_offsets` (62 days) written by the deck and the daemon; new row `~/.cache/cardak/prices.json` | deck, daemon (daily, while running) | Claude entries of LiteLLM's price list), a short "Monthly spend (#29)" section with the measured facts above (dedupe key, `speed`, `iterations`/`advisorModel`, the long-context tier only for haiku-5-5, fast mode unpriced, web search requests not priced, offsets of deleted transcripts not pruned), and the "One interface per external format" line naming `collectors/usage.py`.
- Modify: `PLAN.md` §3 — "Cost dashboards" → "Cost dashboards (cardak shows one monthly figure at API prices, #29; other tools do dashboards well)".
- Modify: `CHANGELOG.md` `## Unreleased` → Added: `The deck and the phone page show what this month's Claude Code usage would cost at API prices, in total and per project (#29)`.

- [ ] **Step 1:** Edit the three files.
- [ ] **Step 2:** `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest --cov --cov-branch --cov-fail-under=80` → all green.
- [ ] **Step 3:** `enola check /home/brlauuu/repos/cardak`; split any new complexity outlier or cycle; record accepted coordinators under "Architecture checks" as `#29 (monthly spend)`.
- [ ] **Step 4: Commit** `docs: monthly spend at API prices (#29)`.
