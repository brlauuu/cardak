# Bug batch 1: agent status — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the agent-status bugs #36, #37, #38, #41, #45, #46 and #56 that make the waiting list or `c` wrong.

**Architecture:** Small changes in `core/agents.py` (handlers, `apply`), `core/store.py` (`mark_seen`, `prune`), `core/hook.py` (logging, quiet summary pings), `core/source.py` (pass the seen wait) and `ui/detail.py` (jump target). No schema change.

**Tech Stack:** Python 3.13, SQLite, Textual.

**Spec:** the issues themselves, plus `docs/superpowers/specs/2026-10-21-m4-agent-awareness-design.md` (M4) and `docs/superpowers/specs/2026-10-21-waiting-summaries-design.md` (#30).

## Global Constraints

- `cardak hook` budget is < 150 ms, it always exits 0 and prints nothing. The import guards in `tests/test_cli.py` stay green.
- No new columns and no schema bump.
- Each commit subject ends with its issue number, e.g. `fix(core): … (#36)`.

## Review Focus

1. An auto-compaction during a *waiting* session must not make it idle, ping again, or lose its summary. Test in Task 1.
2. The deck marks a wait seen while a hook has already moved the session into a newer wait. The newer wait stays unseen. Test in Task 2.
3. A live session with no hook event for more than 7 days that then gets a new event comes back as a fresh row, with no crash. Test in Task 3.
4. A kind change inside one wait requests one summary for the new wait, without a second notification or sound. Test in Task 5.
5. Garbage on the hook's stdin creates no database and logs one line. Test in Task 4.

---

### Task 1: Auto-compaction leaves the status alone (#36)

**Files:** `src/cardak/core/agents.py` (`_on_session_start`), `tests/test_agents.py`

- [ ] Write the failing tests:
  - `test_compact_keeps_working`: working with a current tool, then `SessionStart(source="compact")`. The status, `status_since` and `current_tool` are unchanged.
  - `test_compact_keeps_waiting_and_no_ping`: waiting/finished, then compact. No ping, and the status is the same.
  - `test_resume_and_clear_still_idle`: these sources still go to idle and clear `current_tool`.
- [ ] Run them and watch them fail.
- [ ] Implement: when `e.source == "compact"`, return `replace(s, ended_at=None)`.
- [ ] Run them and watch them pass.
- [ ] Commit `fix(core): auto-compaction no longer shows a working session as idle (#36)`.

### Task 2: Marking seen can't catch a newer wait (#37)

**Files:** `src/cardak/core/store.py` (`mark_seen`), `src/cardak/core/source.py` (both callers), `tests/test_store.py`, `tests/test_source.py`

- **Produces:** `Store.mark_seen(session_id: str, now: float, wait_since: float | None = None) -> None`. Inside the `update` transaction it is a no-op when `wait_since` is not None and differs from the stored `status_since`.
- [ ] Write the failing test `test_mark_seen_skips_a_newer_wait`: the store holds a wait since 10.0. `mark_seen(id, 20.0, wait_since=5.0)` leaves it waiting with `seen_at` None. `wait_since=10.0` marks it.
- [ ] Write the failing source test: both `mark_seen_pane` and the focus path pass the `status_since` they read.
- [ ] Implement the change in `Store.mark_seen` and both callers in `core/source.py`.
- [ ] Run the tests and watch them pass.
- [ ] Commit `fix(core): marking a wait seen no longer catches a newer wait (#37)`.

### Task 3: Prune sessions that never ended (#38)

**Files:** `src/cardak/core/store.py` (`prune`), `tests/test_store.py`

- [ ] Write the failing test `test_prune_drops_stale_live_sessions`. A live row (`ended_at` NULL) with `updated_at` older than `KEEP_SECONDS` is deleted, and a fresh live row stays.
- [ ] Write the test `test_stale_session_comes_back_on_its_next_event`: after pruning, `update` with `apply` creates the row again.
- [ ] Implement in `prune`: `DELETE FROM agent_sessions WHERE ended_at IS NULL AND updated_at < ?`, with the same cutoff as ended rows.
- [ ] Run the tests and watch them pass.
- [ ] Commit `fix(core): prune sessions that crashed without SessionEnd (#38)`.

### Task 4: Log what the hook ignores (#46); the idle_prompt text (#45)

**Files:** `src/cardak/core/hook.py` (`run`, `main`), `src/cardak/core/agents.py` (`IGNORED_EVENTS = frozenset({"SubagentStop"})`), `tests/test_hook.py`; the M4 spec's Notification row

- `main` parses before it opens the Store. When the payload doesn't parse, it logs `ignored: unparseable payload ({n} bytes): {first 200 chars!r}` and returns 0, without creating a database.
- For a parsed event whose name is in neither `HANDLERS` nor `IGNORED_EVENTS`, `run` logs `ignored: unknown event {name}` once per call. The event is still recorded in `events`.
- `run` keeps its signature. `main` passes it the parsed event's text as today.
- [ ] Write the failing tests:
  - `test_garbage_creates_no_db_and_logs`
  - `test_empty_stdin_logs`
  - `test_unknown_event_logged_and_recorded`
  - `test_subagent_stop_not_logged`
- [ ] Implement it, keeping the import guards green (`agents` is already imported).
- [ ] #45, Ruling: no column for it. The raw `Notification` payload, `idle_prompt`'s message included, is already stored in `events`, and nothing reads a per-session copy. Change the M4 spec's Notification row to say "`idle_prompt` and anything else: no status change (the payload stays in `events`)", and close #45 as fixed by documentation. Cost if wrong: a later feature that wants the text reads it from `events`.
- [ ] Commit `fix(core): log unparseable and unknown hook payloads (#46)`, plus `docs: idle_prompt text lives in the events table (#45)`.

### Task 5: A kind change inside a wait gets its own summary (#56)

**Files:** `src/cardak/core/agents.py` (`Ping`, `apply`), `src/cardak/core/hook.py` (`_ping`), `tests/test_agents.py`, `tests/test_hook.py`

- **Produces:** `Ping.quiet: bool = False`.
  - `apply` returns a quiet ping when the session was waiting before and after the event and the kind changed.
  - `_ping` with `quiet` skips `alert` and only spawns the summarizer (when it's on and the key exists), with the new `status_since`.
  - `run`'s "already looking at it" early return applies to quiet pings too.
- [ ] Write the failing tests:
  - `test_kind_change_in_wait_pings_quietly`: permission, then an elicitation notification. Ping quiet, `status_since` moved.
  - `test_same_kind_no_ping`.
  - `test_quiet_ping_spawns_summary_only`: the spawn list has only the summarize argv, and no notify-send or sound.
- [ ] Implement it.
- [ ] Commit `fix(core): a kind change during a wait gets a summary of its own (#56)`.

### Task 6: Enter in the detail panel uses the row's pane (#41)

**Files:** `src/cardak/ui/detail.py` (`DetailScreen.__init__`, `action_jump`), `src/cardak/ui/app.py` (`action_detail`), `tests/test_app.py`

- **Produces:** `DetailScreen(state, project, info=None, pane_id: int | None = None)`. `action_jump` dismisses with `pane_id if pane_id is not None else state.pane_id`, and `action_detail` passes `row.pane_id`.
- [ ] Write the failing test `test_i_enter_jumps_for_a_session_matched_by_process`: the session's `pane_id` is None and the row's pane is 7. Enter jumps to 7.
- [ ] Implement it.
- [ ] Commit `fix(ui): Enter in the detail panel jumps for sessions matched by process (#41)`.

### Task 7: Changelog, AGENTS, gate

- [ ] Add CHANGELOG Unreleased → Fixed: one line per user-visible fix (#36, #37, #38, #41, #46, #56).
- [ ] Add to the AGENTS.md "Agent status" notes: auto-compaction keeps the status, stale live rows are pruned after 7 days, and the hook logs unknown or unparseable payloads.
- [ ] Run the full gate: `uv run pytest --cov --cov-branch --cov-fail-under=80`, ruff check, ruff format --check, pyright, and the `python -X importtime -c "import cardak.cli"` sanity check. Check the hook's timing stays close to its measured median.
- [ ] Commit `docs: agent status fixes in AGENTS.md and the changelog`.
