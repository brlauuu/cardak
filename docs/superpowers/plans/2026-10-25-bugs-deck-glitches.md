# Bug batch 3: deck glitches — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the deck glitches #12, #13, #15, #16, #19, #20, #21, #39, #40, #52, #53 and #55, plus the Docker socket left open when Docker is unreachable (found during batch 2).

**Architecture:** Small, local changes in `ui/app.py`, `ui/widgets.py`, `core/filter.py`, `core/source.py`, `core/navigate.py`, `collectors/wezterm.py`, `collectors/docker.py` and `cli.py`. The one new file is `state_dir()/wezterm_dir`, which records where `cardak.lua` was installed.

**Tech Stack:** Python 3.13, Textual 8.2.

**Spec:** the issues; M1–M4 designs in `docs/superpowers/specs/`.

## Global Constraints

- Labels use `·` as the separator. Free text goes through `Text` or `escape`.
- Collectors import nothing from cardak.
- Commit subjects end with their issue number(s).

## Review Focus

1. A poll that lands right after a find keystroke leaves the cursor on the best match. Test in Task 2.
2. After jumping from "Waiting on you", the cursor goes to that pane's row in its project, not to row 0. Test in Task 2.
3. With `cardak.lua` installed via `--dir`, there is no install hint, and the theme files are written next to it. Test in Task 3.
4. Two quick jumps show the install hint once. Test in Task 3.
5. A `gui-sock-<pid>` that disappears between listing and `stat` is skipped; the poll succeeds. Test in Task 4.

---

### Task 1: Labels say the right thing (#12, #13, #21, #53)

**Files:** `src/cardak/core/snapshot.py` (`DeckSnapshot.internal: bool = False`), `src/cardak/ui/app.py` (`_poll`, `apply_snapshot`, `_empty_label`, `format_title`), `src/cardak/ui/widgets.py` (`internal_label`), `src/cardak/core/source.py` (`_summaries`), tests in `tests/test_app.py`, `tests/test_widgets.py`, `tests/test_source.py`

- **#12:** `_poll` builds `DeckSnapshot(error=f"{e!r}", internal=True)`. `_empty_label` shows `internal_label(reason)` = `"cardak hit an internal error: {reason} · retrying"` (bold red), not the WezTerm label.
- **#13:** the banner shows `snap.config_error` as it is. `ConfigError` already starts with the file name, so drop the `"projects.toml: "` prefix.
- **#21:** `DeckApp.format_title` joins title and subtitle with `" · "`; Textual's default is `" — "`. The unreachable label becomes `"WezTerm not reachable: {reason} · retrying"`.
- **#53:** a `StoreError` in `_summaries` sets `_summary_warning = f"waiting summaries unavailable: {e}"` and leaves `_store_warning` alone.
- [ ] Write the failing tests:
  - `test_internal_error_label`: a `BrokenSource` poll shows "cardak hit an internal error", and not "WezTerm".
  - `test_config_error_banner_names_the_file_once`.
  - `test_header_separator_is_a_dot`: `app.format_title("cardak", "x").plain == "cardak · x"`.
  - `test_summaries_failure_message`.
- [ ] Implement them and run.
- [ ] Commit `fix(ui): labels for internal errors, config errors, summaries and the header (#12, #13, #21, #53)`.

### Task 2: Cursor and counts (#16, #39, #40)

**Files:** `src/cardak/ui/app.py` (`_render`, `_cursor_target`), `src/cardak/core/filter.py` (`filter_snapshot`), `tests/test_app.py`, `tests/test_filter.py`

- **#16:** add `self._follow_pending: bool`. `_render(follow_best=True)` sets it; any `_render` while it is set follows the best match too. The after-refresh move clears it.
- **#39:** `_cursor_target`: when `selected` is `w:<pane>` or `r:<pane>` and that node is gone, fall back to `p:<pane>`.
- **#40:** `filter_snapshot` counts distinct pane ids over the kept groups, so waiting-only mode with a query counts its matches.
- [ ] Write the failing tests:
  - `test_poll_right_after_a_keystroke_keeps_the_best_match`: type a query, then call `apply_snapshot(new_snap)` before a pause. The cursor ends on the best pane.
  - `test_jump_from_waiting_lands_on_the_project_row`.
  - `test_match_count_in_waiting_only_mode`.
- [ ] Implement them and run.
- [ ] Commit `fix(ui): cursor stays on the best match and after a jump from waiting; match count in w mode (#16, #39, #40)`.

### Task 3: Hints and notices (#19, #20, #52, #55)

**Files:** `src/cardak/cli.py` (`_wezterm_install`, new `_lua_dir() -> Path`, every `default_dir()` use for the module), `src/cardak/core/navigate.py` (a `threading.Lock` around the hint check), `src/cardak/core/source.py` (`_summary_store`), `src/cardak/ui/app.py` (cap notice), tests

- **#19:**
  - `cardak wezterm install [--dir X]` writes the resolved directory to `state_dir() / "wezterm_dir"`.
  - `_lua_dir()` returns that directory when `cardak.lua` exists there, else `default_dir()`.
  - The deck's navigator, the module-version check, the theme writer and `cardak themes apply|zsh` all use `_lua_dir()`.
- **#20:** `Navigator.jump` holds a lock across the `lua_installed()` check and the `_hinted` test-and-set.
- **#52:** `_summary_store` also returns None when the key file doesn't exist, so there is no spend line and no summary lines without a key.
- **#55:** `apply_snapshot` sets `_cap_noticed = False` when `summary_capped` is False.
- [ ] Write the failing tests:
  - `test_install_dir_is_remembered_and_used` (CLI with `--dir`, then `_lua_dir()`).
  - `test_hint_once_under_concurrent_jumps` (two threads; `lua_installed` sleeps 50 ms).
  - `test_no_summary_info_without_key`.
  - `test_cap_notice_rearms`.
- [ ] Implement them and run.
- [ ] Commit `fix: install hint with --dir and only once; no spend line without a key; cap notice re-arms (#19, #20, #52, #55)`.

### Task 4: Collectors (#15, Docker socket)

**Files:** `src/cardak/collectors/wezterm.py` (`find_socket`), `src/cardak/collectors/docker.py` (`_UnixConnection.connect`), `tests/test_wezterm.py`, `tests/test_docker.py`

- **#15:** take the socket mtimes in a loop that skips any `stat()` raising `FileNotFoundError`.
- **Docker:** `connect` closes its socket when `sock.connect` raises, then re-raises.
- [ ] Write the failing tests:
  - `test_vanished_gui_sock_is_skipped`: monkeypatch `Path.stat` so it raises for one sock.
  - `test_unreachable_docker_closes_its_socket`: wrap `socket.socket` and record `close`.
- [ ] Implement them and run.
- [ ] Commit `fix(collectors): a vanishing gui-sock is skipped; the Docker socket is closed on failure (#15)`.

### Task 5: Changelog, AGENTS, gate

- [ ] CHANGELOG Unreleased → Fixed: user-facing lines.
- [ ] AGENTS.md files table: a row for `~/.local/state/cardak/wezterm_dir`.
- [ ] Full gate: pytest with coverage, ruff, ruff format, pyright, and `enola check`.
- [ ] Commit `docs: deck glitch fixes in AGENTS.md and the changelog`.
