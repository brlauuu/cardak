# Usage Trends Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Day-by-day Claude Code usage per project over the last 30 days (seven figures), in the deck's history screen (`h`, `c` cycles) and in a "Trends" view on the web page.

**Architecture:** A pure `core/trends.py` turns stored replies and prompts into per-(cwd, local day) sums. The `SpendCounter` builds them with the month's figure each pass and hands them over on `MonthSpend.trends`. `LiveSource` maps cwds to projects (cached, as `_spend_by_group` does) and puts `GroupedTrends` on `DeckSnapshot.usage_trends`. The history screen and `GET /api/trends` both turn that into rows with the same `usage_rows`.

**Tech Stack:** Python 3.13, Textual, SQLite, stdlib `http.server`; the plain-JS page.

**Spec:** `docs/superpowers/specs/2026-10-28-usage-trends-design.md`

## Global Constraints

- Layering `cli → {ui, web, …} → core → collectors`; core never imports ui/web; `cardak hook` imports none of this (`HOOK_FORBIDDEN` in `tests/test_cli.py` gets `cardak.core.trends`).
- Window: `DAYS` = 30 local days ending today (`core/history.DAYS`); days are local calendar dates (`date.fromtimestamp`).
- Cost uses `core/prices.lookup`, `priced` and `reply_cost` per reply, so it adds up like #29. Unpriced or fast-mode replies count toward tokens, not cost, and mark the cell incomplete (`+`).
- Web: only names and numbers go out (no cwd, no session id). Bearer key as the other `/api/*` routes. Text through `textContent`, no `style=` attributes or inline handlers (CSSOM `el.style.x = …` is fine).
- Tests never touch the real db, transcripts, config or network (`tests/conftest.py` already isolates them).
- Public repo: made-up examples in fixtures and tests (webshop, api-gw, notes, /home/user).
- Commits end with the session's Co-Authored-By / Claude-Session lines; subjects end `(#88)`.

## Review Focus

1. A session whose replies span two cwds of one project, or two projects, on one day: counted once per project and once in All projects. Pinned in Task 2 (`test_sessions_are_counted_once_across_cwds`).
2. Midnight: a reply at 23:59 and one at 00:01 land on two local days, and a figure built yesterday is not shown today. Pinned in Task 1 (`test_days_are_local_dates`) and Task 3 (`test_trends_from_yesterday_are_not_shown`).
3. A day with use but nothing to average (replies, no prompts) shows a blank prompt length, not 0. Pinned in Task 2 (`test_averages_blank_without_a_denominator`).
4. Haiku's long-context tier: a day's cost equals the sum of `reply_cost` per reply. Pinned in Task 1 (`test_cost_uses_the_tier_per_reply`).
5. The deck switching RAM → usage → RAM rebuilds the columns, with no stale headers and no crash when there are no trends yet. Pinned in Task 4 (`test_cycle_through_every_figure_and_back`, `test_usage_without_trends_shows_hint`).

---

### Task 1: Per-day usage sums (`core/trends.py`)

**Files:**
- Create: `src/cardak/core/trends.py`
- Test: `tests/test_trends.py`

**Interfaces:**
- Consumes: `collectors.usage.Reply`, `Prompt`; `core.prices.lookup`, `priced`, `reply_cost`; `collectors.prices.ModelPrice`; `core.history.DAYS`.
- Produces:
  ```python
  @dataclass(frozen=True)
  class DayUsage:
      cost: float = 0.0
      unpriced: int = 0          # replies with no price (or fast mode)
      tokens: int = 0            # input + output + cache read + both cache writes
      output_tokens: int = 0
      replies: int = 0
      prompts: int = 0
      prompt_chars: int = 0
      sessions: frozenset[str] = frozenset()
  def merge(a: DayUsage, b: DayUsage) -> DayUsage
  @dataclass(frozen=True)
  class UsageTrends:
      today: date
      cells: Mapping[tuple[str | None, int], DayUsage]  # (cwd, day index 0..DAYS-1, 0 = oldest)
      unpriced: tuple[str, ...]  # same wording as MonthSpend.unpriced
  def window_start(today: date) -> float  # local midnight of today - (DAYS - 1)
  def build_trends(replies: Iterable[Reply], prompts: Iterable[Prompt],
                   prices: Mapping[str, ModelPrice], today: date) -> UsageTrends
  ```
  Rows outside the window are ignored; a reply's session joins `sessions` only when `session_id` is not None.

- [ ] **Step 1: Write the failing tests** in `tests/test_trends.py`, building `Reply`/`Prompt` values directly (a `_reply(ts, cwd="/home/user/webshop", session="s1", model="claude-opus-5-5", inp=10, out=20, …)` helper):
  - `test_sums_per_cwd_and_day`: two replies and one prompt (chars 40) on one day in one cwd → `cells[(cwd, 29)]` has `replies == 2`, `output_tokens == 40`, `prompts == 1`, `prompt_chars == 40`, `tokens` = the sum of all five token fields, `sessions == {"s1"}`.
  - `test_days_are_local_dates`: replies at local 23:59 yesterday and 00:01 today → indexes 28 and 29.
  - `test_rows_outside_the_window_are_ignored`: a reply 30 days before today → no cell.
  - `test_cost_uses_the_tier_per_reply`: two `claude-haiku-5-5` replies, one over 100k context and one under → `cost == reply_cost(p, r1) + reply_cost(p, r2)` (`pytest.approx`).
  - `test_unpriced_and_fast_mode`: a `claude-unknown-1` reply and a `speed="fast"` opus reply → `cost == 0`, `unpriced == 2`, both counted in `tokens`; `trends.unpriced == ("claude-opus-5-5 (fast mode)", "claude-unknown-1")`.
  - `test_merge_adds_and_unions`: `merge` sums every count and unions `sessions`.

- [ ] **Step 2: Run** `uv run pytest tests/test_trends.py -q`. Expected: FAIL, `ModuleNotFoundError: cardak.core.trends`.

- [ ] **Step 3: Implement** the types and functions above. Day index = `(date.fromtimestamp(ts) - first).days` with `first = today - timedelta(days=DAYS - 1)`. Build mutable per-cell accumulators and freeze them at the end; one pass over replies and one over prompts.

- [ ] **Step 4: Run** `uv run pytest tests/test_trends.py -q`. Expected: PASS.

- [ ] **Step 5: Commit** `feat(core): sum Claude Code usage per folder and day (#88)`.

### Task 2: Figures, project rows and bands (`core/trends.py`)

**Files:**
- Modify: `src/cardak/core/trends.py`
- Test: `tests/test_trends.py`

**Interfaces:**
- Consumes: Task 1's `DayUsage`, `UsageTrends`, `merge`; `core.resolver.Resolved`.
- Produces:
  ```python
  Figure = Literal["cost", "tokens", "prompts", "sessions", "prompt_len", "reply_len", "per_session"]
  FIGURES: tuple[Figure, ...] = ("cost", "tokens", "prompts", "sessions",
                                 "prompt_len", "reply_len", "per_session")
  AVERAGED = ("prompt_len", "reply_len", "per_session")
  LABELS: Mapping[Figure, str]  # "cost", "tokens", "prompts", "sessions",
                                # "prompt length", "reply length", "tokens per session"
  ALL = "all"  # subject of the All projects row; core.history.OTHER is Other's
  @dataclass(frozen=True)
  class ProjectTrend:
      subject: str   # "project:<key>" for config/git projects (as core.history), "other", "all"
      name: str      # Resolved.name, "Other", "All projects"
      days: tuple[DayUsage | None, ...]  # DAYS cells, oldest first
  @dataclass(frozen=True)
  class GroupedTrends:
      today: date
      rows: tuple[ProjectTrend, ...]  # projects A–Z, then Other, then All projects
      unpriced: tuple[str, ...]
  def group_trends(trends: UsageTrends, resolve: Callable[[str | None], Resolved]) -> GroupedTrends
  def value(d: DayUsage | None, figure: Figure) -> float | None
  @dataclass(frozen=True)
  class UsageRow:
      subject: str
      name: str
      today: float | None
      per_day: float | None    # mean of value() over the days with use (d is not None)
      peak: float | None
      window: float | None     # value(merge of every day) -> sums, distinct sessions, weighted averages
      days: tuple[float | None, ...]
      incomplete: bool         # cost only: some reply in the window had no price
      incomplete_today: bool
  def usage_rows(grouped: GroupedTrends, figure: Figure) -> list[UsageRow]
  def usage_edges(rows: Sequence[UsageRow]) -> tuple[float, float, float, float]  # ¼ ½ ¾ 1 of the busiest day cell
  ```
  `value`: None for `d is None`; cost → `d.cost`; tokens; prompts; sessions → `len(d.sessions)`; prompt_len → `prompt_chars / prompts`; reply_len → `output_tokens / replies`; per_session → `tokens / len(sessions)`; None when the denominator is 0. Rows with no use in the window are dropped, except that All projects is kept whenever any row exists. `usage_edges` is computed by the caller over the project and Other rows, and separately over the All projects row; it returns `(inf,)*4` when there's no positive cell.

- [ ] **Step 1: Write the failing tests:**
  - `test_group_by_project`: cwds `/home/user/webshop` and `/home/user/webshop/api` resolve to one config project, `/tmp/x` to Other → rows `["webshop", "Other", "All projects"]`; webshop's day cell merges both cwds.
  - `test_sessions_are_counted_once_across_cwds`: session `s1` with replies in both webshop cwds and in api-gw on one day → webshop `value(day, "sessions") == 1`, api-gw `1`, All projects `1`.
  - `test_averages_blank_without_a_denominator`: a day with replies and no prompts → `value(d, "prompt_len") is None`, `value(d, "reply_len") == out / replies`.
  - `test_usage_rows_columns`: three days of prompts 2, 0 (no use: None) and 4 → `per_day == 3`, `peak == 4`, `window == 6`, `today == 4`.
  - `test_window_averages_are_weighted`: day A 1 prompt of 10 chars, day B 3 prompts of 30 chars each → prompt_len `window == 25.0`, `per_day == 20.0`.
  - `test_window_sessions_are_distinct`: one session active on 3 days → sessions `window == 1`, per_session `window == total tokens`.
  - `test_incomplete_cost`: one unpriced reply today → cost row `incomplete` and `incomplete_today` True; tokens row False for both.
  - `test_usage_edges`: busiest cell 8 → `(2.0, 4.0, 6.0, 8.0)`; no positive cell → all `inf`.

- [ ] **Step 2: Run** `uv run pytest tests/test_trends.py -q`. Expected: the new tests FAIL with ImportError on `group_trends`.

- [ ] **Step 3: Implement** the interfaces above in `core/trends.py`. `resolve` gets the cwd string or None; the subject is `f"project:{r.key}"` when `r.kind in core.history.PROJECT_KINDS`, else `OTHER`. Sort projects by `name.lower()`.

- [ ] **Step 4: Run** `uv run pytest tests/test_trends.py -q`. Expected: PASS.

- [ ] **Step 5: Commit** `feat(core): usage figures per project and day (#88)`.

### Task 3: Build trends in the counter; put them on the snapshot

**Files:**
- Modify: `src/cardak/core/spend.py`, `src/cardak/core/snapshot.py`, `src/cardak/core/source.py`, `tests/test_cli.py` (`HOOK_FORBIDDEN`)
- Test: `tests/test_spend.py`, `tests/test_source.py`

**Interfaces:**
- Consumes: Tasks 1 and 2.
- Produces: `MonthSpend.trends: UsageTrends | None = None` (last field, default None). `DeckSnapshot.usage_trends: GroupedTrends | None = None`. Both are set by `SpendCounter.step` / `LiveSource._with_spend`.

- [ ] **Step 1: Write the failing tests:**
  - `test_spend.py::test_step_builds_trends`: a fixture transcript with a reply today → `counter.step().trends.cells` has today's index (29) for the transcript's cwd.
  - `test_spend.py::test_trends_reach_back_into_last_month`: clock on the 3rd, a stored reply (via `store.record_usage`) on the 25th of last month → it's in `trends.cells` but not in `total`.
  - `test_spend.py::test_step_reuses_figures_when_nothing_changed`: two steps with no new lines and the same day and prices → `second is first` (the same `MonthSpend` object).
  - `test_source.py::test_snapshot_carries_grouped_trends`: a `LiveSource` with `spend=lambda: MonthSpend(..., trends=UsageTrends(today, {(cwd, 29): DayUsage(replies=1, prompts=1, prompt_chars=5)}, ()))` and a config project at that cwd → `snap.usage_trends.rows[0].name == "webshop"`.
  - `test_source.py::test_trends_from_yesterday_are_not_shown`: `trends.today` is yesterday for the source's clock → `snap.usage_trends is None`, while `month_spend` is still set when the month matches.
  - `test_source.py::test_grouped_trends_are_cached`: the same `MonthSpend` object on two polls → `snap1.usage_trends is snap2.usage_trends`.
  - Add `"cardak.core.trends"` to `HOOK_FORBIDDEN`.

- [ ] **Step 2: Run** `uv run pytest tests/test_spend.py tests/test_source.py tests/test_cli.py -q`. Expected: the new tests FAIL.

- [ ] **Step 3: Implement:**
  - `SpendCounter.step`: `since = min(month_start(now), window_start(today))`. Load `replies_since(since)` and `prompts_since(window_start(today))` once, build `month_spend` from the replies with `ts >= month_start` and `build_trends` from both. Rebuild only when `_read` stored something this pass (sum of `record_usage`'s return values), the local date changed, or `prices.current()` returned a different object. Otherwise return `self.latest`.
  - `LiveSource._with_spend`: next to `_spend_by_group`, add `_trends_by_group(trends) -> GroupedTrends`, cached on `(trends, resolver)` identity like `_spend_seen`, using `self._resolver.resolve(Path(cwd) if cwd else None)`. Set `usage_trends` only when `trends.today == date.fromtimestamp(self._clock())`. Keep `month_spend`'s own guard as it is.
  - Ruling to ledger: the spec asked for a SQL `GROUP BY`. The counter already loads the month's replies every pass, and the per-reply long-context tier needs Python's `reply_cost`. So it reuses that one load (now ≥ the 30-day window) and skips rebuilding when nothing changed, which is cheaper than today's pass. Check the pass time in Task 6's live run.

- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS (whole suite: `MonthSpend` and `DeckSnapshot` gained defaulted fields).

- [ ] **Step 5: Commit** `feat(core): count usage trends with the month's spend (#88)`.

### Task 4: The history screen's usage figures

**Files:**
- Modify: `src/cardak/ui/history.py`, `src/cardak/ui/widgets.py` (`fmt_count`)
- Test: `tests/test_history_screen.py`, `tests/test_widgets.py`

**Interfaces:**
- Consumes: `GroupedTrends`, `FIGURES`, `LABELS`, `AVERAGED`, `usage_rows`, `usage_edges`, `value`, `UsageRow` (Task 2); `live.usage_trends` (Task 3); `widgets.fmt_usd`.
- Produces: `widgets.fmt_count(n: float) -> str` (`"0"`, `"999"`, `"1.2k"`, `"340k"`, `"1.2M"`, `"3.4B"`; one decimal under 10 of a unit, none above). `HistoryScreen` modes `("ram", "cpu", *FIGURES)`.

- [ ] **Step 1: Write the failing tests:**
  - `test_widgets.py::test_fmt_count`: the values above, from 0, 999, 1_234, 340_000, 1_234_567 and 3_400_000_000.
  - `test_history_screen.py::test_cycle_through_every_figure_and_back`: a live snapshot with `usage_trends` (webshop and Other on two days) → press `c` nine times; after the second press the title contains `cost` and `[c] next: tokens`, and the header row's labels are `today`, `per day`, `peak`, `30 d`; after the ninth press it's RAM again with `now`, `median`, `peak`, `alerts`.
  - `test_usage_rows_and_marks`: on cost, the webshop row shows `fmt_usd` values, and a day with an unpriced reply makes `today` and `30 d` end in `+`; Other comes before All projects whatever the `s` sort.
  - `test_usage_cell_shows_the_day`: selecting webshop's day cell notifies a text containing every `LABELS` value and that day's date, and pushes no `PlotScreen`.
  - `test_usage_without_trends_shows_hint`: `usage_trends=None` → on a usage figure the hint reads `No usage yet: cardak counts it while the deck or the daemon runs.` and the table is empty.
  - `test_all_projects_scaled_on_its_own`: All projects' busiest day is drawn `█` even though it's larger than every project's busiest cell, and so is the busiest project cell.

- [ ] **Step 2: Run** `uv run pytest tests/test_history_screen.py tests/test_widgets.py -q`. Expected: FAIL.

- [ ] **Step 3: Implement:**
  - `HistoryScreen._mode` cycles `MODES = ("ram", "cpu", *FIGURES)`. The binding label becomes `"Next figure"`.
  - `_fill` rebuilds the columns on a mode change (`table.clear(columns=True)`, then add columns). Fixed columns for usage are `("project", 14), ("today", 9), ("per day", 9), ("peak", 9), ("30 d", 9)`; widen `alerts` to 9 too, so both kinds line up with `_axis`.
  - Usage cells use `band(v, edges)` with `usage_edges` over the non-`ALL` rows, and separately for the `ALL` row. The legend reads `<label> per day, relative to the busiest day:` followed by the band ranges in that figure's format, plus `no price: <models>` when `grouped.unpriced`.
  - Formats: cost → `fmt_usd` (+ `"+"` when incomplete); tokens, reply_len and per_session → `fmt_count`; prompts and sessions → integers; prompt_len → `f"{v:.0f} ch"`. None → `-`.
  - The sort keeps `peak/now/median/alerts/name` and maps `now→today`, `median→per_day`, `alerts→window` for usage rows. Other then All projects stay last.
  - Selecting a usage cell: `self.notify` with the project name, the date (`%a %b %-d`) and one `label: value` per figure. A blank day says `no use`.

- [ ] **Step 4: Run** `uv run pytest tests/test_history_screen.py tests/test_widgets.py -q`, then `uv run pytest -q`. Expected: PASS.

- [ ] **Step 5: Commit** `feat(ui): usage trends in the history screen (#88)`.

### Task 5: The web page's Trends view

**Files:**
- Modify: `src/cardak/web/view.py`, `src/cardak/web/server.py`, `src/cardak/web/page.html`
- Test: `tests/test_web_view.py`, `tests/test_web_server.py`, `tests/test_web_page.py`

**Interfaces:**
- Consumes: `usage_rows`, `usage_edges`, `FIGURES`, `LABELS`, `GroupedTrends` (Task 2); `DeckSnapshot.usage_trends` (Task 3).
- Produces: `view.trends_json(grouped: GroupedTrends | None) -> Json`:
  ```
  {"today": "2026-10-28", "first": "2026-09-29", "figures": [{"key": "cost", "label": "cost"}, …],
   "unpriced": [...],
   "rows": [{"name": "webshop", "kind": "project"|"other"|"all",
             "figures": {"cost": {"days": [30 numbers|null], "today": n|null, "per_day": n|null,
                                  "peak": n|null, "window": n|null, "incomplete": bool,
                                  "incomplete_today": bool, "top": n|null}, …}}]}
  ```
  `top` is the bar scale: the busiest day over the non-all rows, and All projects' own for the all row. `None` input gives `{"rows": [], "today": null, …}`. Route `GET /api/trends` (Bearer); 503 `{"starting": true}` before the first snapshot, as `/api/snapshot`.

- [ ] **Step 1: Write the failing tests:**
  - `test_web_view.py::test_trends_json_shape`: the keys above; no `subject`, `cwd` or `sessions` anywhere in `json.dumps(result)`.
  - `test_web_view.py::test_trends_json_none`: `trends_json(None)["rows"] == []`.
  - `test_web_server.py::test_trends_needs_the_key`: no header → 401; with the key → 200 and `rows`.
  - `test_web_page.py::test_page_has_trends_view`: the page contains `id="trends"` (button) and `/api/trends`, no `style=` attribute and no `on…=` attribute (same checks the page test already does for the rest), and the CSP hashes still match (the existing hash test covers it once the script changes).

- [ ] **Step 2: Run** `uv run pytest tests/test_web_view.py tests/test_web_server.py tests/test_web_page.py -q`. Expected: FAIL.

- [ ] **Step 3: Implement:**
  - `view.trends_json` and the route (extend `_route`'s path check; the route doesn't read the session).
  - Page: a "Trends" button in the top bar opens the existing `#sheet` with a `<select>` of figures (change re-renders, no refetch). Each row is the name, a strip of 30 bars (`div.bar` children of a flex container, height set with `bar.style.height = pct + "%"`; empty days get class `none`), and a line `today · per day · peak` formatted like the deck (`usd()` for cost, with `+` when incomplete; a JS `count()` matching `fmt_count`). Fetch on open, then every 30 s while the sheet shows trends; stop when it closes. Fits 390 px: the bar strip takes the full width under the name.
  - The first and last date go under the strip once, at the top of the list.

- [ ] **Step 4: Run** the three test files, then `uv run pytest -q`. Expected: PASS.

- [ ] **Step 5: Commit** `feat(web): usage trends on the phone page (#88)`.

### Task 6: Docs and live check

**Files:**
- Modify: `AGENTS.md` (the Monthly spend section gains a "Trends (#88)" paragraph: where they're built, the `c` cycle, `/api/trends`; the web view section lists `/api/trends`; prompts are now read), `CHANGELOG.md` (Unreleased → Added, one line for the user), `PLAN.md` only if it mentions #88.

- [ ] **Step 1: Live check** on a scratch daemon, as in #29: scratch `XDG_STATE_HOME`, `XDG_CONFIG_HOME`, `XDG_CACHE_HOME` and `CARDAK_CONFIG`; real transcripts read-only; `[web]` and the key written by hand.
  - Time a pass (first and later) against #29's 3.8 s / 70 ms and the daemon's RSS against 50 MB.
  - Check `/api/trends` with curl.
  - Screenshot the Trends sheet in headless Chrome at 390×844 and 1180×820.
  - Kill everything started; never touch the real service.

- [ ] **Step 2: Write the docs**, with the measured numbers.

- [ ] **Step 3: Run** `uv run ruff check && uv run ruff format --check && uv run pyright && uv run pytest --cov --cov-branch --cov-fail-under=80 -q`. Expected: all clean.

- [ ] **Step 4: Run** `enola check .` and split or record any finding, as for #29 (AGENTS.md "Architecture checks").

- [ ] **Step 5: Commit** `docs: usage trends (#88)`.
