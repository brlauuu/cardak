# Bug batch 2: what cardak writes outside the deck — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix #42 (notification HTML), #44 (settings.json formatting), #43 (several GUIs and one focus file) and #54 (an older cardak refuses a newer database).

**Architecture:** One change per area. Each is independent of the others.
- `core/alerts.py`: escape notification bodies.
- `claude/install.py`: keep the file's indent, plus a byte-level round-trip test.
- `wezterm/cardak.lua`: a per-process tmp name.
- `core/store.py`: record the oldest compatible reader in `PRAGMA application_id`.

**Tech Stack:** Python 3.13, SQLite, Lua 5.4 (tests via lupa).

**Spec:** the issues. Plus `docs/superpowers/specs/2026-10-21-m4-agent-awareness-design.md` (hooks install, focus file) and `docs/superpowers/specs/2026-10-21-waiting-summaries-design.md` (schema v2).

## Global Constraints

- No schema bump; `SCHEMA_VERSION` stays 4.
- No `MODULE_VERSION` bump: the Python side doesn't rely on the Lua change.
- `cardak hook` stays within its budget; the store change adds one PRAGMA read.
- Commit subjects end with their issue number.

## Review Focus

1. A reason containing `&`, `<` or `>` reaches the notification as literal text. A body cut at the length limit never leaves half an entity. Test in Task 1.
2. A settings.json written by Claude Code (2-space `JSON.stringify` style, non-ASCII text) is byte-identical after `cardak hooks install` and then `uninstall`. Test in Task 2.
3. A 4-space or tab-indented settings.json keeps its indent. Test in Task 2.
4. A database whose `user_version` is newer than this cardak, but whose minimum reader is not, opens. It is not upgraded and its `user_version` is not lowered. Test in Task 4.
5. A newer database with no minimum reader recorded (`application_id` 0) is still refused with today's message. Test in Task 4.

---

### Task 1: Escape notification bodies (#42)

**Files:** `src/cardak/core/alerts.py` (`notification_argv`, `resource_argv`), `tests/test_alerts.py`

- Cut first, then `html.escape(body, quote=False)`. The summary (title) is plain text in the freedesktop spec, so it stays as it is.
- [ ] Write the failing tests:
  - `test_notification_body_is_escaped`: reason `Bash: cat <in >out && x` → the body contains `&lt;in &gt;out &amp;&amp; x`.
  - `test_resource_body_is_escaped`.
  - `test_cut_then_escaped`: a long reason with `&` near the limit. The body ends with `…` and never with a partial `&am`.
- [ ] Run them and watch them fail; implement; run them and watch them pass.
- [ ] Commit `fix(core): escape notification text, which KDE renders as HTML (#42)`.

### Task 2: settings.json keeps its formatting (#44)

**Files:** `src/cardak/claude/install.py` (`save_settings`, new `_indent(text: str) -> int | str`), `tests/fixtures/settings/claude_written.json` (2-space, fully expanded, with a non-ASCII string), `tests/test_claude_install.py`

- `_indent` returns the leading whitespace of the first indented line: a number of spaces, or `"\t"`. With none, it returns 2.
- `save_settings` reads the existing text, if any, before backing up, and writes `json.dumps(data, indent=_indent(old), ensure_ascii=False) + "\n"`.
- [ ] Write the failing tests:
  - `test_claude_written_settings_round_trip_bytes`: install, then uninstall, against a copy of the fixture. The bytes are identical.
  - `test_four_space_and_tab_indent_kept`.
  - `test_inline_arrays_are_expanded` documents the remaining difference: hand formatting is not kept, only the content.
- [ ] Implement; run them and watch them pass.
- [ ] Note in the README hooks section: "Your settings are kept as data; hand formatting such as inline arrays is expanded".
- [ ] Commit `fix(claude): settings.json keeps its indent, byte-identical for files Claude Code wrote (#44)`.

### Task 3: One tmp file per WezTerm process (#43)

**Files:** `src/cardak/wezterm/cardak.lua` (`write_focus_history`), `tests/test_lua.py`

- The tmp name becomes `focus_file .. '.tmp.' .. pid`, with `pid` from `pcall(wezterm.procinfo.pid)`. It falls back to plain `.tmp` when that is unavailable.
- Two GUIs still share the `focus` history; the last writer wins. Document that in AGENTS.md ("Several WezTerm GUIs").
- [ ] Write the failing tests. The fake `wezterm` gets `procinfo = { pid = function() return 4242 end }` and `io.open` is wrapped to record names:
  - `test_focus_tmp_is_per_process`: the opened name ends in `focus.tmp.4242`, and the final file is written.
  - `test_focus_tmp_without_procinfo`: the plain `.tmp` still works.
- [ ] Implement; run them and watch them pass.
- [ ] Commit `fix(wezterm): each GUI writes the focus file through its own tmp file (#43)`.

### Task 4: An older cardak can open a compatible newer database (#54)

**Files:** `src/cardak/core/store.py` (`Store.__init__`, `MIN_READER`), `tests/test_store.py`

- `MIN_READER = 1`: every schema so far only adds tables, so schema-1 code reads a v4 database fine.
- On open:
  - `version <= SCHEMA_VERSION`: as today, plus `PRAGMA application_id = MIN_READER`.
  - `version > SCHEMA_VERSION`: read `application_id`. If it is between 1 and `SCHEMA_VERSION`, open as is: no `_SCHEMA` script, no version change, WAL on. Otherwise refuse with today's message.
- Comment by `SCHEMA_VERSION`: a migration that changes or drops something older code reads must raise `MIN_READER`.
- Existing releases (≤ 0.9.0) still refuse newer files. The fix works from this version on; the CHANGELOG says so.
- [ ] Write the failing tests:
  - `test_new_store_records_min_reader`.
  - `test_newer_compatible_db_opens_untouched`: set `user_version` 9 and `application_id` 1. It opens, and `user_version` stays 9.
  - `test_newer_db_without_min_reader_refused`.
  - `test_newer_db_needing_newer_reader_refused`: `application_id` 9.
- [ ] Implement; run them and watch them pass.
- [ ] Commit `fix(core): a newer but compatible cardak.db opens in an older cardak (#54)`.

### Task 5: Changelog, AGENTS, gate

- [ ] CHANGELOG Unreleased → Fixed: lines for #42, #43, #44 and #54, written for users.
- [ ] AGENTS.md:
  - the files table row for `cardak.db` mentions `application_id`;
  - the Themes/focus note covers several GUIs;
  - the Claude Code hooks note covers formatting.
- [ ] Full gate: pytest with coverage, ruff, ruff format, pyright.
- [ ] Commit `docs: batch 2 fixes in AGENTS.md, README and the changelog`.
