# Context Usage Over Time Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show each session's context over time (main thread only, log scale) in the deck's history screen, as a day plot, on agent rows, and on the phone's Trends sheet with a month plot.

**Architecture:** The usage collector learns which replies are a subagent's (`isSidechain`); the store keeps those keys in a new table (older readers unaffected) and back-fills it once from the window's transcripts. A pure `core/context.py` turns stored replies into per-session lines and per-day numbers; the spend counter builds it with the trends, `LiveSource` groups it per project, and the deck and the web view draw it.

**Tech Stack:** Python 3.13, SQLite, Textual/Rich, vanilla JS + inline SVG.

**Spec:** `docs/superpowers/specs/2026-10-29-context-usage-design.md`

## Global Constraints

- Context of a reply = `input_tokens + cache_read_tokens + cache_write_5m_tokens + cache_write_1h_tokens`; replies with 0 are dropped; subagent (sidechain) replies are left out of everything this feature shows.
- Drop rule: a reply's context is below half of the previous main-thread reply's, and that one was over 100k (`DROP_FROM = 100_000`, `DROP_RATIO = 0.5`).
- Ended: a session ends on the local day of its last main-thread reply, only once that reply is an hour old (`ENDED_AFTER_S = 3600`); "without a drop" = no drop over its whole life.
- Log scale 10k → 1M, gridlines/labels at 10k, 100k, 1M (`TICKS`); below 10k sits on the bottom, above 1M on the top.
- Spend and usage trends must not change: the backfill writes only the new flag table, never `claude_replies`.
- Store: only new tables, so `MIN_READER` stays 1; `SCHEMA_VERSION` 5 → 6.
- Nothing new in `cardak hook`'s imports; `core` stays free of `ui`/`web`; transcript parsing only in `collectors/usage.py`.
- Web: no session ids (an index per response), no cwd paths; text via `textContent`; no `style=` attributes or inline handlers.
- Tests never touch `~/.claude` or real state (conftest already isolates).
- Commit trailer: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_019HSHPSZYnNNv9YWY6Gwi7a`. No home paths or other repos in commits.

## Review Focus

1. A day with sessions in two projects plus a subagent burst: the plot must show only the main threads, each in its own color, and the All projects row all of them.
2. A session spanning midnight: counted in both days' session numbers, its line drawn in each day's part, ended on one day only.
3. A session still in use (last reply < 1 h old) must not count as ended, and must start counting once it's an hour old even if no new reply arrives (the counter's rebuild key includes a 10-minute time bucket). Test in Task 4.
4. The counter's first run after the upgrade, with a month of rows stored and no flags: the backfill flags subagent keys, spend and trends are identical before and after (test in Task 2 compares `month_spend` and `build_trends` outputs).
5. The phone payload for a busy month stays small: points per session capped (`MAX_POINTS = 300`) while keeping both sides of every drop (test in Task 6).

---

### Task 1: Subagent flag in the collector and the store

**Files:**
- Modify: `src/cardak/collectors/usage.py` (`Reply`, `_reply`)
- Modify: `src/cardak/core/store.py` (schema, `record_usage`, `replies_since`, `prune`, new methods)
- Test: `tests/test_usage.py`, `tests/test_store.py`

**Interfaces:**
- Produces: `Reply.sidechain: bool = False` (last field); `Store.flag_sidechains(keys: Iterable[str]) -> None`; `Store.backfilled(name: str) -> bool`; `Store.mark_backfilled(name: str, now: float) -> None`; `replies_since` returns replies with `sidechain` set from the new table.

- [ ] **Step 1: Failing tests**

```python
# tests/test_usage.py
def test_sidechain_replies_are_flagged() -> None:
    sub = read_usage(FIXTURES / "sub" / "agent-x.jsonl", 0)[0].replies
    main = read_usage(FIXTURES / "session.jsonl", 0)[0].replies
    assert sub and all(r.sidechain for r in sub)
    assert main and not any(r.sidechain for r in main)

# tests/test_store.py
def test_sidechain_flag_round_trips(tmp_path) -> None:
    s = Store(tmp_path / "c.db")
    main, sub = reply("a"), replace(reply("b"), sidechain=True)
    s.record_usage("/t.jsonl", 1, 10, Usage((main, sub), ()))
    assert {r.key: r.sidechain for r in s.replies_since(0)} == {"a": False, "b": True}

def test_flag_sidechains_marks_stored_replies(tmp_path) -> None:
    s = Store(tmp_path / "c.db")
    s.record_usage("/t.jsonl", 1, 10, Usage((reply("a"), reply("b")), ()))
    s.flag_sidechains(["b", "zzz"])
    assert {r.key: r.sidechain for r in s.replies_since(0)} == {"a": False, "b": True}

def test_backfill_mark(tmp_path) -> None:
    s = Store(tmp_path / "c.db")
    assert not s.backfilled("sidechains")
    s.mark_backfilled("sidechains", 5.0)
    assert Store(tmp_path / "c.db").backfilled("sidechains")

def test_schema_6_keeps_min_reader_1(tmp_path) -> None:
    Store(tmp_path / "c.db")
    con = sqlite3.connect(tmp_path / "c.db")
    assert con.execute("PRAGMA user_version").fetchone()[0] == 6
    assert con.execute("PRAGMA application_id").fetchone()[0] == 1

def test_prune_drops_flags_of_pruned_replies(tmp_path) -> None:
    # a sidechain reply older than USAGE_KEEP_SECONDS: after prune, flag table has no row for it
```
(`reply(key)` is the module's existing reply helper, or a small one building a `Reply` with `ts` near `NOW`.)

- [ ] **Step 2: Run, expect FAIL** — `uv run pytest tests/test_usage.py tests/test_store.py -q` → `sidechain` unknown / no such table.

- [ ] **Step 3: Implement**
  - `_reply`: `sidechain=record.get("isSidechain") is True`.
  - Schema: `CREATE TABLE IF NOT EXISTS claude_sidechain_replies (reply_key TEXT PRIMARY KEY);` and `CREATE TABLE IF NOT EXISTS usage_backfills (name TEXT PRIMARY KEY, done_at REAL NOT NULL);`; `SCHEMA_VERSION = 6`; comment that both are new tables so `MIN_READER` stays.
  - `record_usage`: insert `astuple(r)[:11]` (the 11 stored columns; name the slice `_REPLY_COLUMNS = 11`), and `INSERT OR IGNORE` the sidechain keys in the same transaction.
  - `replies_since`: `SELECT r.*, s.reply_key IS NOT NULL FROM claude_replies r LEFT JOIN claude_sidechain_replies s USING (reply_key) WHERE r.ts >= ? ORDER BY r.ts, r.rowid`.
  - `prune`: `DELETE FROM claude_sidechain_replies WHERE reply_key NOT IN (SELECT reply_key FROM claude_replies)` after the replies cutoff.
  - `flag_sidechains`: one transaction, `INSERT OR IGNORE`.

- [ ] **Step 4: Run, expect PASS**, then the whole suite: `uv run pytest -q`.

- [ ] **Step 5: Commit** `feat(store): remember which replies are a subagent's (#86)`

### Task 2: One-time backfill in the spend counter

**Files:**
- Modify: `src/cardak/core/spend.py`
- Test: `tests/test_spend.py`

**Interfaces:**
- Consumes: Task 1's store methods, `read_usage`, `transcript_files`, `trends.window_start`.
- Produces: `spend.SIDECHAINS = "sidechains"`; `SpendCounter._backfill(today: date) -> None`, called at the top of `step` until done.

- [ ] **Step 1: Failing tests**

```python
def test_backfill_flags_replies_stored_before_the_flag(tmp_path, root) -> None:
    c = counter(tmp_path, root)
    store = c.store
    # rows as an older cardak stored them: no flags, offsets at the end
    for path in root.rglob("*.jsonl"):
        u, end = read_usage(path, 0)
        store.record_usage(str(path), path.stat().st_ino, end,
                           Usage(tuple(replace(r, sidechain=False) for r in u.replies), u.prompts))
    store._con.execute("DELETE FROM claude_sidechain_replies")
    before = (month_spend(store.replies_since(0), BUILTIN, month_start(NOW)).total,)
    c.step()
    flagged = {r.key for r in store.replies_since(0) if r.sidechain}
    assert flagged == {k for k, r in all_replies(root).items() if r.sidechain}
    assert (c.latest.total,) == before          # spend unchanged
    assert store.backfilled("sidechains")

def test_backfill_runs_once(tmp_path, root, monkeypatch) -> None:
    # second step does not call read_usage for the backfill (count calls via monkeypatch)

def test_backfill_on_an_empty_store_reads_nothing(tmp_path, root, monkeypatch) -> None:
    # no replies stored yet: marked done without a backfill read (normal pass flags as it goes)

def test_backfill_writes_no_replies(tmp_path, root) -> None:
    # store holds one reply; after _backfill the claude_replies count is still 1
```

- [ ] **Step 2: Run, expect FAIL** — `uv run pytest tests/test_spend.py -q`.

- [ ] **Step 3: Implement** `_backfill`: if `self._backfilled` return; if `store.backfilled(SIDECHAINS)` set and return; if `store.usage_mark() == (0, 0)` mark and return; else for each `transcript_files(root, window_start(today))` read from 0 to its size with `read_usage` in `READ_LIMIT` chunks, collect keys of `sidechain` replies, `flag_sidechains` in `BATCH`-sized lists; then `mark_backfilled(SIDECHAINS, now)`. It never calls `record_usage`.

- [ ] **Step 4: Run, expect PASS**, whole suite green.

- [ ] **Step 5: Commit** `feat(spend): flag subagent replies stored before #86, once (#86)`

### Task 3: `core/context.py`

**Files:**
- Create: `src/cardak/core/context.py`
- Test: `tests/test_context.py`

**Interfaces:**
- Consumes: `Reply`, `trends.DAYS`, `trends.first_day`, `trends.window_start`, `trends.OTHER`, `trends.ALL`, `trends.PROJECT_KINDS`, `Resolved`.
- Produces:
  - constants `DROP_FROM = 100_000`, `DROP_RATIO = 0.5`, `ENDED_AFTER_S = 3600`, `LOW = 10_000`, `HIGH = 1_000_000`, `TICKS = (10_000, 100_000, 1_000_000)`, `EDGES = (50_000, 100_000, 200_000, 400_000)` (history bands, my call).
  - `context_tokens(r: Reply) -> int`
  - `@dataclass(frozen=True) SessionLine(session_id: str, cwd: str | None, points: tuple[tuple[float, int], ...])` (oldest first)
  - `@dataclass(frozen=True) ContextSeries(today: date, built_at: float, lines: tuple[SessionLine, ...], latest: Mapping[str, int])`
  - `build_context(replies: Iterable[Reply], today: date, now: float) -> ContextSeries` — window replies only, no sidechain, no 0, no `session_id None`; a line's cwd is its first reply's; `latest[session_id]` = last point's context.
  - `drop_indices(points: Sequence[tuple[float, int]]) -> list[int]` — indices i ≥ 1 meeting the drop rule.
  - `@dataclass(frozen=True) DayContext(peak: int, sessions: int, drops: int, ended: int, ended_clean: int)`
  - `day_context(lines: Sequence[SessionLine], today: date, now: float) -> tuple[DayContext | None, ...]` — `DAYS` cells, None for a day no line touches.
  - `@dataclass(frozen=True) ProjectContext(subject: str, name: str, lines: tuple[SessionLine, ...], days: tuple[DayContext | None, ...])`
  - `@dataclass(frozen=True) GroupedContext(today: date, built_at: float, rows: tuple[ProjectContext, ...])` — projects A–Z, Other, All projects (as `group_trends`).
  - `group_context(series: ContextSeries, resolve: Callable[[str | None], Resolved]) -> GroupedContext` (uses `series.built_at` as now for `day_context`).
  - `position(v: float) -> float` — `log10` between `LOW` and `HIGH`, clamped to [0, 1].
  - `day_points(line: SessionLine, day: date) -> list[tuple[float, int]]` — that local day's points (for plots and text).

- [ ] **Step 1: Failing tests** (synthetic `Reply`s built with a helper `r(ts, ctx, sid="s1", cwd="/home/user/webshop", sidechain=False)` putting `ctx` in `cache_read_tokens`)

```python
def test_sidechain_and_zero_replies_are_left_out(): ...  # only main, >0 points remain
def test_drop_rule():
    pts = [(0, 300_000), (1, 140_000), (2, 150_000), (3, 90_000), (4, 40_000)]
    assert drop_indices(pts) == [1]           # 90k→40k: from under 100k, not a drop
    assert drop_indices([(0, 100_001), (1, 50_000)]) == [1]
    assert drop_indices([(0, 100_001), (1, 50_001)]) == []
def test_day_numbers():  # one day: two sessions, one compacted twice, one ended low an hour+ ago
    # → DayContext(peak=..., sessions=2, drops=2, ended=1, ended_clean=1)
def test_recent_session_is_not_ended():  # last reply 30 min before now → ended 0
def test_session_across_midnight():  # sessions counted on both days, ended on the second only
def test_compacted_session_ended_is_not_clean():  # ended 1, ended_clean 0
def test_group_context_rows_order_and_all():  # projects A–Z, Other, All projects; All has every line
def test_position():
    assert position(10_000) == 0 and position(1_000_000) == 1
    assert position(100_000) == pytest.approx(0.5)
    assert position(5) == 0 and position(5_000_000) == 1
def test_latest_is_the_last_main_reply(): ...
def test_replies_outside_the_window_are_left_out(): ...
```

- [ ] **Step 2: Run, expect FAIL** — `uv run pytest tests/test_context.py -q` → module missing.
- [ ] **Step 3: Implement** the module, pure (no I/O), docstring like `core/trends.py`'s.
- [ ] **Step 4: Run, expect PASS**; `uv run pyright src/cardak/core/context.py`.
- [ ] **Step 5: Commit** `feat(core): context per session and day, from stored replies (#86)`

### Task 4: Counter builds it, the snapshot carries it

**Files:**
- Modify: `src/cardak/core/spend.py` (`MonthSpend.context`, `step`)
- Modify: `src/cardak/core/snapshot.py` (`AgentView.context: int | None = None`, `DeckSnapshot.usage_context: GroupedContext | None = None`)
- Modify: `src/cardak/core/source.py` (`_with_spend`, `_context_by_group`, agent context on rows)
- Test: `tests/test_spend.py`, `tests/test_source.py`

**Interfaces:**
- Consumes: Task 3's `build_context`, `group_context`.
- Produces: `MonthSpend.context: ContextSeries | None = None`; `DeckSnapshot.usage_context`; `AgentView.context` (the session's latest main-thread context, or None).

- [ ] **Step 1: Failing tests**

```python
# test_spend.py
def test_step_builds_context(tmp_path, root):
    c = counter(tmp_path, root); c.step()
    assert c.latest.context is not None
    assert all(not k.startswith("msg_s") for l in c.latest.context.lines for k in [l.session_id])
def test_step_rebuilds_context_every_ten_minutes(tmp_path, root):
    # clock advanced 601 s with nothing new: latest is a new object (ended counts can change)

# test_source.py
def test_agent_rows_carry_context(...)   # spend stub whose context.latest has the pane's session → AgentView.context == value
def test_usage_context_only_for_today(...)  # a context built yesterday is not put on the snapshot
def test_usage_context_cached_per_series_and_resolver(...)  # group_context called once for two polls
```

- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** — `step`'s `built_on` gains `int(now // 600)`; `context=build_context(replies, today, now)` in the `replace`. `LiveSource._with_spend` puts `usage_context` beside `usage_trends` (same today check, cached like `_trends_by_group`), and replaces every `PaneRow.agent` (all groups, the waiting group included) with `context=series.latest.get(session_id)`.
- [ ] **Step 4: Run, expect PASS**, whole suite.
- [ ] **Step 5: Commit** `feat(source): context per project and per agent on the snapshot (#86)`

### Task 5: Deck — agent rows, history mode, day plot

**Files:**
- Modify: `src/cardak/ui/widgets.py` (`pane_label`)
- Modify: `src/cardak/ui/plot.py` (`braille_cells`; `braille` built on it)
- Modify: `src/cardak/ui/history.py` (mode `"context"`, `_fill_context`, highlight note, Enter → plot)
- Create: `src/cardak/ui/context_plot.py` (`ContextPlotScreen`)
- Test: `tests/test_widgets.py`, `tests/test_plot.py`, `tests/test_history_screen.py`

**Interfaces:**
- Consumes: `GroupedContext`, `ProjectContext`, `DayContext`, `position`, `day_points`, `drop_indices`, `TICKS`, `EDGES`.
- Produces: `plot.braille_cells(values: Sequence[float | None], top: float, width: int, height: int) -> list[list[int]]` (bit masks); `ContextPlotScreen(row: ProjectContext, day: date, today: date)`; `history.context_note(name: str, day: date, d: DayContext | None) -> str`.

Copy (exact):
- agent row: `  ctx 312k` (dim, `fmt_count`), after the status text; nothing when `context` is None.
- history title name: `context`; columns `project`, `today`, `per day`, `peak`, `drops` (today's peak, mean of daily peaks, 30-day peak, 30-day drop count).
- legend: `peak context per day:  ░ <50k  ▒ 50k–100k  ▓ 100k–200k  ▓ 200k–400k  █ 400k+`
- note: `Oct 28: 5 sessions · peak 597k · 4 drops · 2 ended without one`; a day without use: `Oct 28: no sessions`.
- plot title: `<name> · context · <Wed Oct 28>`; y labels `1M`, `100k`, `10k` left of their gridline rows; under the plot one line per session: `● 14:02–17:45  peak 412k · 2 drops · last 98k` with the bullet in the session's color.

- [ ] **Step 1: Failing tests**

```python
# test_widgets.py
def test_agent_row_shows_context(): ...  # AgentView(context=312_000) → "ctx 312k" in pane_label text
def test_agent_row_without_context(): ...  # no "ctx"

# test_plot.py
def test_braille_cells_matches_braille(): ...  # chr(0x2800 + bits) of braille_cells == braille lines

# test_history_screen.py (existing pilot patterns)
async def test_c_reaches_context_mode(): ...  # title shows "context" after cycling past the usage figures
async def test_context_cells_use_fixed_log_bands(): ...  # 40k → ░, 150k → ▓ (band 2), 500k → █
async def test_highlight_shows_the_day_note(): ...  # #hint text == context_note(...)
async def test_enter_opens_the_day_plot(): ...  # ContextPlotScreen pushed for that row and day
async def test_plot_left_right_moves_day_and_draws_each_session(): ...  # two sessions → two colors in the plot Text spans; ← changes title day
def test_context_note_copy(): ...  # exact strings above
```

- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement**
  - Plot: x = the day's dot columns (`width * 2`, `width = max(48, min(size.width - 8, 120))`), y = `position(ctx)` with `top = 1.0`, `PLOT_HEIGHT` rows. Per session a dense column list: each point's column gets the max of its points; columns between a session's first and last point that day are forward-filled with the previous value, so its line is continuous and a drop draws as a vertical stroke. Merge sessions by OR-ing `braille_cells`; a character takes the color of the last session that set bits in it. Gridlines `─` (dim) on blank cells of `TICKS` rows (`level_row(position(t), 1.0, PLOT_HEIGHT)`). Palette: `("cyan", "magenta", "yellow", "green", "blue", "red", "bright_cyan", "bright_magenta")`, by the session's order of first reply that day.
  - History: `MODES = ("ram", "cpu", *FIGURES, "context")`; `Mode` includes `Literal["context"]`; `_fill` dispatches; rows from `live.usage_context.rows` that have any day; sort as `sort_usage` does (biggest first, Other/All last); `on(DataTable.CellHighlighted)` in context mode updates `#hint` with the note; `_open_day` in context mode pushes `ContextPlotScreen`.
- [ ] **Step 4: Run, expect PASS**, whole suite, pyright.
- [ ] **Step 5: Commit** `feat(ui): context in the history screen, a day plot, and on agent rows (#86)`

### Task 6: Phone — context on the Trends sheet

**Files:**
- Modify: `src/cardak/web/view.py` (`context_json`, `trends_json(grouped, context=None)`)
- Modify: `src/cardak/web/server.py` (pass `snap.usage_context`)
- Modify: `src/cardak/web/page.html`
- Test: `tests/test_web_view.py`, `tests/test_web_page.py`, `tests/test_web_server.py`

**Interfaces:**
- Consumes: `GroupedContext`, `drop_indices`, `TICKS`, `window_start`.
- Produces: `/api/trends` JSON gains `figures` entry `{"key": "context", "label": "context"}` (only when context is present) and
  `"context": {"start": <unix s of the window's first local midnight>, "ticks": [10000, 100000, 1000000], "rows": [{"name", "kind", "peak", "drops", "days": [{"peak","sessions","drops","ended","ended_clean"} | null ×30], "lines": [[[t, ctx], ...], ...]}]}` where `t` is seconds since `start`; `MAX_POINTS = 300` per line.

- [ ] **Step 1: Failing tests**

```python
def test_context_json_has_no_ids_or_paths(): ...   # json.dumps(result) contains no session id and no "/home/user"
def test_context_json_days_and_lines(): ...         # rows, 30 days, lines as offsets from start
def test_context_lines_are_capped_but_keep_drops(): # 2_000 points with drops at 700 and 1500 → ≤ 300 points, both sides of each drop kept
def test_trends_json_lists_context_figure_only_with_data(): ...
# test_web_page.py
def test_page_has_context_view(): ...  # page contains 'figure === "context"', "createElementNS", "ended without one"
# test_web_server.py
def test_api_trends_carries_context(): ...  # snapshot with usage_context → body["context"]["rows"]
```

- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement**
  - Downsampling: keep indices `i-1, i` of every drop, first and last, then evenly spaced others up to `MAX_POINTS`.
  - Page, when `figure === "context"`: per row a box with `name`, `30 d peak <k> · <n> drops`, one bar per day (height `position(peak)`, computed in JS with the same formula), a **month plot** SVG (viewBox `0 0 300 120`; one `polyline` per line with classes `s0`…`s7`; dashed gridlines with labels at the ticks; date labels for first, middle, last day), and a selected-day plot SVG under it with the day's note text (same copy as the deck). Tapping a bar or the month plot selects that day (`Math.floor(x / width * 30)`); the selection survives the 30 s refresh. CSS classes for the eight stroke colors go in the page's style block (the CSP hash test recomputes).
- [ ] **Step 4: Run, expect PASS**, whole suite; headless-Chrome check at 390×844 and 1180×820 against a scratch daemon with synthetic transcripts (scratch XDG dirs, never the real service).
- [ ] **Step 5: Commit** `feat(web): context per session and day on the Trends sheet (#86)`

### Task 7: Docs, live check, architecture

**Files:**
- Modify: `AGENTS.md` (Layout: `core/context.py`, `ui/context_plot.py`; a `### Context usage (#86)` section under Monthly spend: sidechains, the drop rule and its 29/30 check, ended, the two new tables and the backfill, the `/api/trends` field; the cardak.db table row; enola accepted findings)
- Modify: `README.md` (history screen and Trends sheet), `CHANGELOG.md` (Unreleased → Added: one line, #86)

- [ ] **Step 1:** Live deck check against a copy of synthetic transcripts with scratch `XDG_STATE_HOME`/`XDG_CONFIG_HOME`/`CARDAK_CONFIG`: history `c` → context, highlight, Enter, ←/→.
- [ ] **Step 2:** `enola check .` / `diff_snapshot`; split anything new over complexity 10 or record it in AGENTS.md.
- [ ] **Step 3:** `uv run ruff check && uv run ruff format --check && uv run pyright && uv run pytest --cov --cov-branch --cov-fail-under=80 -q` → all pass.
- [ ] **Step 4: Commit** `docs: context usage (#86)`
