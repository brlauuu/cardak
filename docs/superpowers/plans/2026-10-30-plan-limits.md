# Plan limits on the deck (#70) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show the 5-hour and weekly plan limits (what's left, when each resets) as one coloured row of bars in the deck and on the phone page, read by wrapping the user's Claude Code status line.

**Architecture:** `cardak statusline --then '<original>'` becomes `statusLine.command`; it parses Claude Code's status line JSON (`collectors/limits.py`), merges the reading into `~/.local/state/cardak/limits.json` under a lock (`core/limits.py`), then runs the original command with the same stdin. `LiveSource` reads the file when its mtime changes onto `DeckSnapshot.plan_limits`; the deck draws it in a `#limits` Static, the web view sends it in `snapshot_json` and the page draws it under the machine line. `claude/statusline.py` installs/uninstalls the wrapper in `~/.claude/settings.json`.

**Tech Stack:** Python 3.13, Textual, stdlib only (`fcntl`, `json`, `shlex`, `subprocess`).

**Spec:** `docs/superpowers/specs/2026-10-30-plan-limits-design.md`

## Global Constraints

- Layering: `collectors` import nothing from cardak; `core` never imports `ui`, `cardak.web`, `cardak.claude`; `claude/` imports nothing from cardak outside `cardak.claude` (as `claude/skills.py` already does).
- Parsing of Claude Code's status line JSON only in `collectors/limits.py`; `claude/` stays the only writer under `~/.claude/`.
- `cardak statusline` is dispatched in `cli.main` before argparse; it must not import anything in `tests/test_cli.py::HOOK_FORBIDDEN`, nor `textual`, `sqlite3`, `cardak.core.store`.
- Any failure in cardak's part still runs the original command with the original stdin bytes and relays its stdout and exit code; problems go to `hook.log`.
- Thresholds: yellow below 20% left, red below 10% left; stale label after 900 s (`· read N min ago`); a write at most every 60 s unless a window changed.
- Tests never touch the real `~/.claude/settings.json`, state dir or status line (scratch `--settings`, `XDG_STATE_HOME` from `tests/conftest.py`).
- Commits end with the session's attribution lines; conventional commit subjects ending `(#70)`.

## Review Focus

1. Status line input with `rate_limits` absent, `null`, or one window missing → original output unchanged, nothing written, stored windows kept. (Task 1, Task 3 tests.)
2. Original command that fails, prints to stderr, or exits non-zero → same stdout and exit code relayed. (Task 3 `test_relays_exit_code`.)
3. Original command containing quotes, `$`, pipes → round-trips through `--then` and `uninstall` byte for byte. (Task 4 `test_wrap_round_trips_awkward_commands`.)
4. `limits.json` corrupt or half-written → deck treats it as no reading, wrapper overwrites it with a fresh one. (Task 2 `test_load_ignores_corrupt_file`.)
5. A window whose `resets_at` has passed shows "—", never a stale %. (Task 2 `test_window_past_reset_is_unknown`.)

---

### Task 1: Parse the status line input (`collectors/limits.py`)

**Files:**
- Create: `src/cardak/collectors/limits.py`
- Create: `tests/fixtures/statusline/plan.json` (shape from the docs, neutral values: model, workspace `/home/user/webshop`, `rate_limits.five_hour {used_percentage: 73.0, resets_at: 1793370000}`, `seven_day {used_percentage: 39.0, resets_at: 1793700000}`), `tests/fixtures/statusline/no_limits.json` (same without `rate_limits`)
- Test: `tests/test_limits_collector.py`

**Interfaces:**
- Produces: `Window(used: float, resets_at: float)` (frozen dataclass), `Reading(five_hour: Window | None, seven_day: Window | None)`, `parse_statusline(data: bytes) -> Reading | None` (None when the input isn't JSON, has no `rate_limits` object, or neither window is valid).

- [ ] **Step 1: Tests**: `test_parses_both_windows` (plan.json → `Reading(Window(73.0, 1793370000), Window(39.0, 1793700000))`), `test_no_rate_limits_is_none`, `test_garbage_is_none` (`b"\xff{"`), `test_one_window_missing` (only `seven_day` → `five_hour is None`), `test_bad_window_values_dropped` (`used_percentage: "x"`, negative, > 100 is clamped to 100, missing `resets_at` → that window None).
- [ ] **Step 2:** Run `uv run pytest tests/test_limits_collector.py -q`. Expected: FAIL (module missing).
- [ ] **Step 3:** Implement. `bool` is not a number here (`isinstance(v, bool)` rejected).
- [ ] **Step 4:** Run again. Expected: PASS.
- [ ] **Step 5:** Commit `feat(collectors): read plan limits from the status line input (#70)`.

### Task 2: Store, merge and view the limits (`core/limits.py`)

**Files:**
- Create: `src/cardak/core/limits.py`
- Test: `tests/test_limits.py`

**Interfaces:**
- Consumes: `Window`, `Reading` from Task 1; `config.state_dir`.
- Produces:
  - `MIN_WRITE_S = 60`, `STALE_S = 900`, `LOW_LEFT = 20`, `CRITICAL_LEFT = 10`.
  - `Stored(read_at: float, five_hour: Window | None, seven_day: Window | None)`.
  - `limits_path(env: Mapping[str, str] | None = None) -> Path` (`state_dir(env) / "limits.json"`).
  - `merge(old: Stored | None, new: Reading, now: float) -> Stored` — per window: later `resets_at` replaces, same `resets_at` keeps the higher `used`, earlier is ignored, a missing new window keeps the old one. `read_at = now` if any window of `new` set or matched the stored one, else `old.read_at`.
  - `needs_write(old: Stored | None, merged: Stored, now: float) -> bool` — windows differ, or `now - old.read_at >= MIN_WRITE_S` and `merged.read_at == now`.
  - `load(path: Path) -> Stored | None` (missing/corrupt/wrong shape → None).
  - `record(path: Path, reading: Reading, now: float) -> None` — `flock` on `path.with_suffix(".lock")`, load, merge, write atomically (temp file + `os.replace`, mode 0600) when `needs_write`.
  - `LimitView(left: int | None, resets_at: float | None, level: Literal["ok", "low", "critical", "unknown"])`, `LimitsView(five_hour: LimitView, seven_day: LimitView, age_s: float)`, `limits_view(stored: Stored, now: float) -> LimitsView` (left = `round(100 - used)`; `resets_at <= now` → unknown; level by `LOW_LEFT`/`CRITICAL_LEFT`).

- [ ] **Step 1: Tests**: `test_merge_keeps_higher_within_window` (old 73 / new 60 same reset → 73, `read_at` unchanged), `test_merge_new_window_replaces` (later reset with 5 → 5, `read_at == now`), `test_merge_ignores_older_window`, `test_merge_keeps_missing_window`, `test_needs_write_throttles` (same windows, 30 s later → False; 61 s → True), `test_record_round_trips` (tmp_path), `test_record_two_writers_keep_the_higher` (two `record` calls in sequence, lower second), `test_load_ignores_corrupt_file` (`b"{"` → None; `record` then overwrites it), `test_window_past_reset_is_unknown`, `test_levels` (left 20 → ok, 19 → low, 9 → critical), `test_written_file_is_private` (mode 0o600).
- [ ] **Step 2:** Run `uv run pytest tests/test_limits.py -q`. Expected: FAIL.
- [ ] **Step 3:** Implement. JSON keys: `read_at`, `five_hour`, `seven_day`, each `{"used", "resets_at"}` or `null`.
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 5:** Commit `feat(core): keep the latest plan limits across sessions (#70)`.

### Task 3: The wrapper (`cardak statusline`)

**Files:**
- Create: `src/cardak/core/statusline.py`
- Create: `src/cardak/core/hooklog.py` (move `LOG_MAX` and `log_problem` out of `core/hook.py`; `hook.py` imports them from there, so nothing else changes)
- Modify: `src/cardak/cli.py` (`main`: `argv[:1] == ["statusline"]` dispatches before `build_parser`; also add a `statusline` subparser group for Task 4 so `--help` lists it)
- Test: `tests/test_statusline.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `parse_statusline` (Task 1), `record`, `limits_path` (Task 2).
- Produces: `main(argv: list[str], stdin: BinaryIO, stdout: BinaryIO, env: Mapping[str, str] | None = None, clock: Callable[[], float] = time.time) -> int`. `argv` is what follows `statusline`: `[]` or `["--then", "<command>"]`; anything else → log it, print nothing, return 0.

- [ ] **Step 1: Tests**: `test_records_and_relays` (plan.json in, `--then "printf hi"` → stdout `b"hi"`, `limits.json` written), `test_original_gets_same_stdin` (`--then "cat"` → stdout equals input bytes), `test_relays_exit_code` (`--then "echo out; echo err >&2; exit 3"` → stdout `b"out\n"`, returns 3), `test_no_then_prints_nothing`, `test_no_limits_writes_nothing` (no_limits.json → no file, original still runs), `test_record_failure_still_runs_original` (`XDG_STATE_HOME` pointing at a file, so mkdir fails → original output relayed, `hook.log` absent or not required, returns original's code), `test_statusline_imports_only_what_it_needs` in `tests/test_cli.py` (subprocess like the hook's, `sys.argv = ['cardak', 'statusline']`, stdin bytes via `sys.stdin = io.TextIOWrapper(io.BytesIO(...))`; forbidden: `HOOK_FORBIDDEN` + `("textual", "sqlite3", "cardak.core.store")`).
- [ ] **Step 2:** Run `uv run pytest tests/test_statusline.py tests/test_cli.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3:** Implement. Read all of stdin as bytes first; wrap parse+record in `try/except Exception` → `log_problem(env, traceback)`; then `subprocess.run(["sh", "-c", cmd], input=data, stdout=PIPE, env=env)` with stderr inherited, write its stdout, return its returncode. `cli.main` passes `sys.stdin.buffer`, `sys.stdout.buffer`.
- [ ] **Step 4:** Run the tests and `uv run python -X importtime -c "import cardak.cli" 2>&1 | tail -3` and time 20 runs of `printf '%s' "$(cat tests/fixtures/statusline/plan.json)" | XDG_STATE_HOME=<scratch> .venv/bin/cardak statusline --then true` against `.venv/bin/python -c pass`. Expected: PASS; cardak's median overhead under 100 ms (record the number for Task 6).
- [ ] **Step 5:** Commit `feat(core): cardak statusline saves plan limits and runs your status line (#70)`.

### Task 4: Install and uninstall (`claude/statusline.py`, `cardak statusline install|uninstall|status`)

**Files:**
- Create: `src/cardak/claude/statusline.py`
- Modify: `src/cardak/cli.py` (`_statusline(command, settings_arg, yes) -> int`, reusing `_ask` and the no-keyboard behaviour of `_write_settings`; `status` also prints the last reading's age from `core.limits.load`)
- Test: `tests/test_claude_statusline.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `load_settings`, `save_settings`, `backup_path`, `find_executable`, `SettingsError` from `cardak.claude.install`.
- Produces:
  - `wrapped_command(exe: Path, original: str | None) -> str` → `"<quoted exe> statusline"` or `"<quoted exe> statusline --then <shlex.quote(original)>"`.
  - `unwrap(command: str) -> tuple[bool, str | None]` → `(True, original)` when `command` is cardak's (argv[0] basename `cardak`, argv[1] `statusline`, optionally `--then X`), else `(False, None)`.
  - `with_statusline(settings: Settings, exe: Path) -> Settings` — already cardak's → unchanged; `statusLine` missing → `{"type": "command", "command": wrapped(exe, None)}`; `type == "command"` → only `command` replaced; other type or non-dict → `SettingsError`.
  - `without_statusline(settings: Settings) -> Settings` — cardak's with an original → command restored; without → `statusLine` removed; not cardak's → unchanged.
  - `statusline_status(settings: Settings) -> str | None` → the wrapped original (`""` when none) or None when not installed.
  - `changes(before: Settings, after: Settings) -> list[str]` → `["- statusLine.command: <old>", "+ statusLine.command: <new>"]` (omit missing sides).

- [ ] **Step 1: Tests**: `test_wraps_existing_command_keeps_other_keys` (`{"type":"command","command":"npx -y somestatus@latest","padding":0}` → padding kept, `unwrap` gives the original), `test_wrap_round_trips_awkward_commands` (`printf '%s' "$HOME" | tr a-z A-Z; echo 'x'"y"` → install then uninstall equals the input dict), `test_install_twice_is_unchanged`, `test_no_statusline_added_then_removed`, `test_other_type_refused`, `test_uninstall_foreign_is_unchanged`, `test_status`; CLI: `test_statusline_install_writes_backup` (scratch `--settings`, `--yes`), `test_statusline_uninstall_nothing_to_do` (prints "not installed", writes nothing), `test_statusline_install_without_keyboard_writes_nothing` (as the hooks test for #80 does).
- [ ] **Step 2:** Run `uv run pytest tests/test_claude_statusline.py tests/test_cli.py -q`. Expected: new tests FAIL.
- [ ] **Step 3:** Implement. Install's done message: "Done. Claude Code sessions report plan limits to the deck after their next reply. This wraps your status line as a workaround (#97)."
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 5:** Commit `feat(claude): cardak statusline install wraps the status line (#70)`.

### Task 5: Snapshot, deck row and phone row

**Files:**
- Modify: `src/cardak/core/snapshot.py` (`DeckSnapshot.plan_limits: LimitsView | None = None`)
- Modify: `src/cardak/core/source.py` (`LiveSource.__init__(…, limits_file: Path | None = None)`; `_with_limits(snap)` called from `_poll`: `stat` the file, reload with `load` only when `st_mtime_ns` changed, `limits_view(stored, clock())` every poll; missing file → None). The deck and the daemon pass `limits_path(env)` in `cli.py`.
- Modify: `src/cardak/ui/widgets.py` (`limits_label(v: LimitsView, now: float) -> Text`), `src/cardak/ui/app.py` (`#limits` Static after `#machine`, `display` only when `plan_limits` is set)
- Modify: `src/cardak/web/view.py` (`_limits(snap) -> Json | None`: `{"five_hour": {"left", "resets_at", "level"}, "seven_day": …, "age_s"}`), `src/cardak/web/page.html` (`<span id="limits">` after `#machine`, `renderLimits`, classes `.lim-low` / `.lim-critical` / `.lim-unknown`)
- Test: `tests/test_source.py`, `tests/test_widgets.py`, `tests/test_app.py`, `tests/test_web_view.py`, `tests/test_web_page.py`

**Interfaces:**
- Consumes: `LimitsView`, `LimitView`, `limits_view`, `load`, `limits_path`, `STALE_S` (Task 2).
- Produces: `limits_label(v: LimitsView, now: float) -> Text` — `5h <bar> 27% left · 16:40   week <bar> 61% left · Mon 09:00`, bars 15 cells of `█`/`░` for what's left, unknown → `5h — `; reset time `%H:%M` when within 24 h, else `%a %H:%M`; `· read N min ago` appended when `age_s > STALE_S`; style per level `""` / `"yellow"` / `"red"` / `"dim"`.

- [ ] **Step 1: Tests**: `test_limits_label` (fixed `now`, local times via `datetime(...).timestamp()`; exact string above and spans' styles), `test_limits_label_unknown_and_stale`, `test_source_reads_limits_when_changed` (tmp file, two polls, rewrite → new value; deleted → None), `test_deck_shows_limits_row` (app pilot with a snapshot carrying `plan_limits` → `#limits` displayed, one line), `test_snapshot_json_limits` (no paths, the keys above, None when absent), `test_page_has_limits_row` (`id="limits"`, `renderLimits`), and a node test `test_limits_text_matches_the_deck` comparing the page's text builder with `limits_label(...).plain` for one case.
- [ ] **Step 2:** Run the listed test files. Expected: new tests FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the full gate: `uv run ruff check && uv run ruff format --check && uv run pyright && uv run pytest -q`. Expected: all pass.
- [ ] **Step 5:** Commit `feat(ui,web): plan limits row in the deck and on the phone (#70)`.

### Task 6: Docs, live check, enola

**Files:**
- Modify: `README.md` (install line `cardak statusline install`; workaround note → #97; only the user-level settings file is wrapped), `AGENTS.md` (Layout lines for the new modules; files table rows for `limits.json`, `limits.lock` and the `statusLine` entry in `~/.claude/settings.json`; a `### Plan limits (#70)` section: source, merge rule, budget measured in Task 3, workaround → #97), `CHANGELOG.md` (Unreleased → Added, user-facing).

- [ ] **Step 1:** Write the docs.
- [ ] **Step 2:** Live check in scratch `HOME`/`XDG_*` under the scratchpad: `cardak statusline install --settings <scratch>/settings.json --yes` over a scratch settings file with a status line; pipe plan.json through the installed command; run a scratch daemon with the web view on a free port and the deck headless; screenshot the phone page at 390×844 and 1180×820. If a scratch `claude` session can be driven with `--settings <scratch>` and a status line, capture one real input as a sanitized fixture and confirm the field shape. Expected: row shows in both, the user's real settings and service untouched.
- [ ] **Step 3:** `enola check` (baseline pinned before Task 1); split anything over complexity 10 or record accepted findings under "Architecture checks" as `#70`.
- [ ] **Step 4:** Full gate as in Task 5. Expected: all pass.
- [ ] **Step 5:** Commit `docs: plan limits (#70)`.
