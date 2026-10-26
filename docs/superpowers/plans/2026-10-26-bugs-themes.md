# Bug batch 4: themes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the M3 theme bugs #22, #23, #24, #25, #27 and #28, and close #26, which is already fixed.

**Architecture:**
- `ui/theme_picker.py` and `ui/app.py`: preview and save order, and scheme loading.
- `wezterm/schemes.py`: an empty cache.
- `wezterm/generate.py`: duplicate paths, paths that would break the tints format.
- `cli.py`: `themes zsh` problems.

**Tech Stack:** Python 3.13, Textual, Lua (unchanged).

**Spec:** the issues; `docs/superpowers/specs/2026-10-20-m3-themes-design.md`.

## Global Constraints

- `wezterm/` imports nothing from cardak except `cardak.core.config`/`themes` as today.
- No `MODULE_VERSION` bump: `cardak.lua` doesn't change.
- Commit subjects end with their issue number(s).

## Review Focus

1. Saving a scheme never sends an empty preview first. A save that fails, or saves the current scheme, leaves no preview behind. Test in Task 1.
2. Two projects with the same path: the Lua table and the deck's accent use the same, first, project, and the user is told. Test in Task 2.
3. An empty but valid cache makes `load_schemes` probe again, and `read_cached_schemes` return None. Test in Task 3.
4. Opening the picker while the startup probe is running doesn't start a second probe. Test in Task 3.
5. A project path with a tab or newline is left out of `tints`, and the rest of the file is intact. Test in Task 2.

---

### Task 1: No flash on save (#22)

**Files:** `src/cardak/ui/theme_picker.py` (`_choose`), `src/cardak/ui/app.py` (`action_theme`'s `done`), `tests/test_theme_picker.py`, `tests/test_app.py`

- `_choose` stops the debounce timer but does not send `""`. Saving reloads the config, and a reload clears every preview (`cardak.lua`).
- The deck's `done` clears the preview when `set_theme` raises `ConfigError`, and when the chosen scheme equals the group's current theme (no write, so no reload). It clears it with `self.previewer.preview(path, "")`, only when a previewer exists.
- [ ] Write the failing tests:
  - `test_enter_saves_without_clearing_first`: after Enter, the last preview call is not `""`.
  - `test_failed_save_clears_the_preview`.
  - `test_saving_the_current_scheme_clears_the_preview`.
- [ ] Update `test_enter_saves_and_clears_preview`, which asserts the old behaviour.
- [ ] Implement, then run the tests.
- [ ] Commit `fix(ui): saving a scheme no longer flashes the old colors (#22)`.

### Task 2: Generated files (#23, #28)

**Files:** `src/cardak/wezterm/generate.py` (`render_projects_lua`, `render_tints`), `tests/test_generate.py`

- **#23:** both renderers keep only the first project for each path. `render_projects_lua` reports `duplicate path {path}: {later name} is ignored, {first name} is used`. The resolver already prefers the first, so the deck and WezTerm agree.
- **#28:** `render_tints` skips a path containing `\t`, `\n` or `\r`. `render_projects_lua` is unaffected, since `lua_str` escapes them. The problem is reported as `{name}: its path can't go in the tints file (tab or newline)`. Only `render_projects_lua` returns problems today, so `write_generated` collects it through a new `tint_problems(config) -> list[str]`.
- [ ] Write the failing tests:
  - `test_duplicate_paths_first_wins_and_is_reported`.
  - `test_tints_skip_paths_with_tab_or_newline`.
  - `test_write_generated_reports_tint_problems`.
- [ ] Implement, then run the tests.
- [ ] Commit `fix(wezterm): duplicate project paths theme like the deck; odd paths stay out of tints (#23, #28)`.

### Task 3: Scheme loading (#24, #25)

**Files:** `src/cardak/wezterm/schemes.py` (`load_schemes`, `read_cached_schemes`), `src/cardak/ui/app.py` (`_load_schemes`, `_schemes_for_picker`, `threading.Lock`), `tests/test_schemes.py`, `tests/test_app.py`

- **#24:** a cache that parses to no schemes counts as missing. `read_cached_schemes` returns None, and `load_schemes` probes again.
- **#25:**
  - The deck holds `self._scheme_lock`. `_load_schemes` and `_schemes_for_picker` both take it, and re-check `self.schemes.get()` once inside.
  - `_schemes_for_picker`, after a real load, calls `self.source.regenerate()` and `call_from_thread(self.action_refresh)`. It runs in the picker's worker thread.
- [ ] Write the failing tests:
  - `test_empty_cache_is_probed_again`.
  - `test_empty_cache_is_not_trusted_by_read`.
  - `test_picker_load_regenerates`.
  - `test_one_probe_for_startup_and_picker`: a `load_schemes` that sleeps and counts calls; open the picker while it runs; the count is 1.
- [ ] Implement, then run the tests.
- [ ] Commit `fix: an empty scheme cache is re-probed; the picker's load regenerates and never doubles a probe (#24, #25)`.

### Task 4: `cardak themes zsh` prints problems (#27)

**Files:** `src/cardak/cli.py` (`_themes_zsh`), `tests/test_cli.py`

- Print each problem from `write_generated` as `warning: …` on stderr, as `themes apply` does.
- [ ] Write the failing test `test_themes_zsh_prints_problems`: a foreign `cardak_projects.lua` beside an installed module.
- [ ] Implement, then run the test.
- [ ] Commit `fix(cli): cardak themes zsh prints what it couldn't write (#27)`.

### Task 5: Changelog, gate, close #26

- [ ] Add CHANGELOG Unreleased → Fixed lines.
- [ ] Close #26 with a pointer to `test_startup_loads_schemes_and_regenerates`: the probe's completion already regenerates.
- [ ] Run the full gate and `enola check`.
- [ ] Commit `docs: theme fixes in the changelog`.
