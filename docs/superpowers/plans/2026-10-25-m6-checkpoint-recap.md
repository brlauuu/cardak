# M6: Checkpoint & Recap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/checkpoint` and `/where-were-we` as Claude Code skills that cardak installs, plus a deck key that sends `/checkpoint` to an agent at its prompt (`c`) and a local recap view (`r`).

**Architecture:** Two new read-only collectors (`handoff.py` parses `.cardak/handoff.md`, `git.py` runs read-only git). `collectors/wezterm.py` gains `send_text`. Pure decisions live in `core/checkpoint.py` (which pane, may it take text now) and `core/recap.py` (which notes, newest first, what changed since). The skills are Markdown files inside the package, installed by `claude/skills.py` via `cardak skills …`. The UI adds a confirm dialog and a recap screen.

**Tech Stack:** Python 3.13, Textual (`Markdown`, `OptionList`, `ModalScreen`), git, `wezterm cli send-text`, uv_build (package data ships as in `sounds/`).

**Spec:** `docs/superpowers/specs/2026-10-24-m6-checkpoint-recap-design.md`

## Global Constraints

- Layering: `cli → {ui, claude, …} → core → collectors`; collectors import nothing from cardak; `claude/` imports nothing from cardak outside `cardak.claude`.
- All WezTerm calls through `collectors/wezterm.py`, always `--no-auto-start`.
- Handoff path: `<dir>/.cardak/handoff.md`, `<dir>` = `git rev-parse --show-toplevel` of the session's directory (a worktree's own top level), else the directory. Each worktree keeps its own note.
- Entry heading: `## YYYY-MM-DD HH:MM` (local time), then ` · <branch>` in a git repo. File preamble: `# Handoff notes (written by /checkpoint, read by /where-were-we and cardak)`. `.cardak/.gitignore` contains `*`.
- cardak never writes the handoff file; the deck runs only `git --no-optional-locks` read commands with a timeout, no `gh`, no network.
- Skills install target `~/.claude/skills/<name>/SKILL.md`; marker line `<!-- cardak: generated -->` directly after the frontmatter; ask before writing (or `--yes`); refuse unmarked files without `--force`; write only on change, atomically.
- Send: `wezterm cli --no-auto-start send-text --pane-id N --no-paste "/checkpoint\r"` (verified 2026-10-25 in a headless mux: the pane receives exactly `b'/checkpoint\r'`).
- Confirm dialog copy: `Send /checkpoint to pane {id} · {project} · {label}?` and `Anything already typed in that pane's input is sent with it.`
- Keys: `c` Checkpoint, `r` Recap, `R` Refresh.
- Commits: conventional, subject ends `(#64)`, trailers as in AGENTS/session. Fixtures sanitized (`/home/user/...`, `webshop`, `api-gw`).
- Tests never touch `~/.claude` or the real state dir; live checks use scratch dirs and a headless mux, no windows.

## Review Focus

1. A handoff file with CRLF endings, a BOM, no trailing newline, or `## ` sub-headings an agent wrote inside an entry still yields the right entries (only date headings start one). Test in Task 1.
2. An `AskUserQuestion` menu the user has already *seen* (state idle via `mark_seen`, menu still open) must refuse `c`. Test in Task 3.
3. A pane cwd that no longer exists, or a project path that isn't a git repo, must not break the recap; it shows what it can. Tests in Tasks 2 and 6.
4. Free text from the note, branch names or commit subjects containing `[x]` must not be read as Rich markup in the recap screen. Test in Task 7.
5. A skill file at the target that the user wrote (no marker) is never overwritten or removed without `--force`, including by `uninstall`. Test in Task 4.

---

### Task 1: Handoff file parser

**Files:**
- Create: `src/cardak/collectors/handoff.py`
- Create: `tests/fixtures/handoff/handoff.md`, `tests/fixtures/handoff/crlf.md` (written by the test from the first with `\r\n` and a BOM)
- Test: `tests/test_handoff.py`

**Interfaces:**
- Produces:
  - `NOTE = Path(".cardak") / "handoff.md"`
  - `@dataclass(frozen=True) class Entry: when: datetime; branch: str | None; body: str` (`body` = text after the heading line, stripped)
  - `parse_entries(text: str) -> list[Entry]`: file order (oldest first).
  - `read_note(top: Path) -> list[Entry] | None`: `None` when the file is absent; `[]` when present but has no entries; never raises on `OSError`/`UnicodeDecodeError` (→ `None`).

- [ ] **Step 1: Write the fixture and failing tests**

Fixture: preamble line, three entries (`## 2026-10-20 09:05 · main`, `## 2026-10-22 17:40` without branch, `## 2026-10-24 14:10 · 64-checkpoint`), the last with a `## Notes` line and a fenced block inside its body.

```python
def test_entries_in_file_order(): ...
    assert [e.when for e in es] == [datetime(2026,10,20,9,5), datetime(2026,10,22,17,40), datetime(2026,10,24,14,10)]
    assert [e.branch for e in es] == ["main", None, "64-checkpoint"]
def test_subheading_stays_in_body():
    assert "## Notes" in es[-1].body and len(es) == 3
def test_crlf_bom_no_trailing_newline(): same three entries
def test_preamble_and_junk_are_not_entries():
    assert parse_entries("# Handoff notes\n\nsome text\n") == []
def test_bad_date_heading_is_body(): "## 2026-13-40 99:99" doesn't start an entry
def test_read_note_absent_and_unreadable(tmp_path): None for no file; None for a directory at NOTE; [] for an empty file
```

- [ ] **Step 2: Run** `uv run pytest tests/test_handoff.py -v`. Expected: FAIL, module not found.
- [ ] **Step 3: Implement.** Regex `^## (\d{4}-\d{2}-\d{2} \d{2}:\d{2})(?: · (.+))?\s*$`, validated with `datetime.strptime`; strip a leading `﻿`; `splitlines()` handles CRLF.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat(collectors): parse .cardak/handoff.md (#64)`

### Task 2: Read-only git

**Files:**
- Create: `src/cardak/collectors/git.py`
- Test: `tests/test_git.py` (real `git` in `tmp_path` repos, with `GIT_CONFIG_GLOBAL=/dev/null`, author env set in the test)

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Commit: sha: str; ts: float; subject: str`
  - `@dataclass(frozen=True) class GitState: branch: str | None; changed: int; untracked: int; commits: tuple[Commit, ...]` (commits newest first)
  - `toplevel(path: Path, runner: Runner = subprocess.run) -> Path | None`
  - `state(top: Path, since: float | None, limit: int = 20, runner: Runner = subprocess.run) -> GitState | None` (`None`: not a repo or git failed; `since=None`: no commits listed)
  - `Runner` as in `collectors/wezterm.py`.
- Every call: `["git", "--no-optional-locks", "-C", str(dir), ...]`, `capture_output=True, text=True, timeout=3`; `FileNotFoundError`, `TimeoutExpired`, `NotADirectoryError` and non-zero exit → `None`.
- Log: `log -n {limit} --since=@{int(since)} --format=%h%x1f%ct%x1f%s`. Status: `status --porcelain` (`??` lines are untracked). Branch: `branch --show-current` (empty → `None`, detached).

- [ ] **Step 1: Failing tests:** `test_toplevel_of_subdir`, `test_toplevel_of_worktree_is_its_own` (`git worktree add`), `test_toplevel_outside_git_is_none`, `test_toplevel_missing_dir_is_none`, `test_state_counts_changed_and_untracked`, `test_log_since_newest_first_and_limit` (commits with `GIT_COMMITTER_DATE` set; `since` between them), `test_detached_branch_is_none`, `test_timeout_is_none` (fake runner raising `TimeoutExpired`).
- [ ] **Step 2: Run** `uv run pytest tests/test_git.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(collectors): read-only git state for recaps (#64)`

### Task 3: Sending, and when a session may take it

**Files:**
- Modify: `src/cardak/collectors/wezterm.py` (add `send_text` after `activate_pane`)
- Modify: `src/cardak/core/agents.py:157-163` (`_on_pre_tool`)
- Create: `src/cardak/core/checkpoint.py`
- Test: `tests/test_wezterm.py`, `tests/test_agents.py`, `tests/test_checkpoint.py`

**Interfaces:**
- Produces:
  - `wezterm.send_text(pane_id: int, text: str, env: Mapping[str, str] | None = None, runner: Runner = subprocess.run) -> None`, which runs `_run_cli(["send-text", "--pane-id", str(pane_id), "--no-paste", text], …)`.
  - `checkpoint.COMMAND = "/checkpoint\r"`
  - `checkpoint.refusal(agent: AgentView | None) -> str | None`
  - `checkpoint.target(group: Group, row: PaneRow | None) -> PaneRow | str` (a str is the refusal shown in a notice)
- `_on_pre_tool` for `AskUserQuestion` also sets `current_tool=ASK_TOOL`. Today it keeps the previous tool's summary, so a pending menu can't be told from a finished turn. `Stop` and `UserPromptSubmit` already clear it, and `PostToolUse` overwrites it.
- Copy (exact):
  - no agent: ``"No status for this session (run `cardak hooks install`, then start a new session)."``
  - working: `"Claude is working; checkpoint when it's done."`
  - permission: `"Claude is waiting for a permission answer."`
  - menu: `"Claude is showing a question; answer it first."`
  - not Claude: `"Select a Claude session."`
  - none in project: `"No Claude session in {name}."`
  - several: `"Several Claude sessions in {name}: select one."`
- Rule: refuse when `agent is None`; when `status == "working"`; when `kind == "permission"`; when `current_tool == ASK_TOOL`, whatever the status. Otherwise allow (idle; waiting `finished`; waiting `question` from a Stop).
- `target`:
  - a row → it, if `is_claude`, else "Select a Claude session.";
  - a `config`/`git` group → its single Claude pane, or the none/several copy;
  - a `waiting`/`other` group → "Select a Claude session."
  - Then apply `refusal` to the chosen row's agent.

- [ ] **Step 1: Failing tests:**
  - `test_send_text_args`: a fake runner sees `["wezterm","cli","--no-auto-start","send-text","--pane-id","12","--no-paste","/checkpoint\r"]`.
  - `test_ask_sets_current_tool`: PreToolUse AskUserQuestion → `current_tool == "AskUserQuestion"`.
  - `test_seen_menu_still_refused`: AskUserQuestion, then `mark_seen` → idle, and `refusal` is the menu copy.
  - `test_refusal_table`: parametrized over each status/kind with its exact copy, including allowed `None` for idle, finished, and question with `current_tool=None`.
  - `test_target_*`: pane, project with one, none, several, waiting header.
- [ ] **Step 2: Run** `uv run pytest tests/test_wezterm.py tests/test_agents.py tests/test_checkpoint.py -v`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement.** Check that `tests/test_cli.py::test_hook_imports_only_what_it_needs` still passes; `agents` gains no imports.
- [ ] **Step 4: Run** the full suite. Expected: PASS.
- [ ] **Step 5: Commit** `feat(core): when a session can take /checkpoint; wezterm send-text (#64)`

### Task 4: The two skills and their installer

**Files:**
- Create: `src/cardak/claude/skills/checkpoint/SKILL.md`, `src/cardak/claude/skills/where-were-we/SKILL.md`
- Create: `src/cardak/claude/skills.py`
- Test: `tests/test_skills.py`

**Interfaces:**
- Produces (`cardak.claude.skills`):
  - `NAMES = ("checkpoint", "where-were-we")`, `MARKER = "<!-- cardak: generated -->"`
  - `default_dir() -> Path` = `Path.home() / ".claude" / "skills"`
  - `bundled(name: str) -> str` via `importlib.resources.files("cardak.claude") / "skills" / name / "SKILL.md"`
  - `SkillStatus = Literal["installed", "outdated", "missing", "foreign"]`; `status(dir: Path, name: str) -> SkillStatus`
  - `class SkillsError(Exception)`
  - `install(dir: Path, force: bool) -> list[Path]`: written files (unchanged ones skipped). Raises `SkillsError` naming every foreign file when `not force`, before writing anything.
  - `uninstall(dir: Path) -> list[Path]`: removes marked files only, then their directory if empty.
  - `hint(dir: Path) -> str | None`: `"Run \`cardak skills install\` to add /checkpoint and /where-were-we to Claude Code."` when any skill is `missing`. `foreign` counts as present.
- Atomic write: tempfile beside the target, then `os.replace`, the same as `claude/install.save_settings`. Factor a shared `_write_atomic` in `claude/install.py` if that keeps it one copy.
- SKILL.md content follows the spec's "The skills" section.
  - Frontmatter `name` and `description`, then the marker line, then the instructions.
  - Before writing frontmatter, look up the current Claude Code skill frontmatter keys (claude-code-guide agent). Use `argument-hint` for `/checkpoint`'s optional note if supported. Don't use keys you can't confirm.
  - `/checkpoint` carries one literal example entry in a fenced `markdown` block, in exactly the heading format.

- [ ] **Step 1: Failing tests:**
  - `test_bundled_frontmatter_and_marker`: line 0 `---`, `name: <dir name>`, the marker on the line after the closing `---`.
  - `test_checkpoint_example_parses`: extract the fenced example, `parse_entries` → 1 entry with a branch.
  - `test_checkpoint_mentions_rules`: `.cardak/handoff.md`, `.gitignore`, `*`, `--show-toplevel`, and "never commit" in any case.
  - `test_where_were_we_mentions_sources`: `git log`, `gh`, `handoff.md`.
  - `test_install_fresh_then_unchanged`: the second call returns `[]`.
  - `test_install_refuses_foreign_writes_nothing`: one foreign file, the other skill missing → `SkillsError`, the missing one still absent.
  - `test_install_force_replaces_foreign`.
  - `test_outdated_status`.
  - `test_uninstall_leaves_foreign_and_other_files`.
  - `test_hint`.
- [ ] **Step 2: Run** `uv run pytest tests/test_skills.py -v`. Expected: FAIL.
- [ ] **Step 3: Write both SKILL.md files and `skills.py`.**
- [ ] **Step 4: Run** the tests, then `uv build` and `unzip -l dist/*.whl | grep SKILL.md`. Expected: PASS, and both files are in the wheel. Delete `dist/` afterwards.
- [ ] **Step 5: Commit** `feat(claude): /checkpoint and /where-were-we skills and their installer (#64)`

### Task 5: `cardak skills install | uninstall | status`

**Files:**
- Modify: `src/cardak/cli.py`: parser block after `hooks`; dispatch through `_setup`; `_skills(command, dir, yes, force)`; `_skills_hint()`.
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: Task 4.
- Produces: `cli._skills_hint() -> str | None` (the deck's `skills_status`).
- Flags:
  - `install`: `--yes`, `--force`, `--dir PATH`. Default `skills.default_dir()`.
  - `uninstall`: `--yes`, `--dir`.
  - `status`: `--dir`.
- `install` prints each target with its status, and asks `Write {n} file(s) under {dir}? [y/N]` unless `--yes` or nothing would change. When nothing would change it prints `cardak skills already installed: {dir}`. On `SkillsError` it prints `cardak: …` to stderr and returns 1.
- `status` prints one line per skill: `{name}: {installed|outdated (run \`cardak skills install\`)|not installed|not cardak's}: {path}`.
- Keep `cli.main`'s complexity flat. `skills` goes through `_setup` with the other file-writing commands.

- [ ] **Step 1: Failing tests** (`--dir tmp_path`, `monkeypatch` `input`):
  - `test_skills_install_asks_and_writes`.
  - `test_skills_install_no_answer_writes_nothing`.
  - `test_skills_install_foreign_exit_1`.
  - `test_skills_status_lines`.
  - `test_skills_uninstall`.
  - `test_skills_hint_none_when_installed` (`HOME` → tmp).
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full suite. Expected: PASS. Also check `cardak hook`'s import guard.
- [ ] **Step 5: Commit** `feat(cli): cardak skills install, uninstall and status (#64)`

### Task 6: Recap data

**Files:**
- Create: `src/cardak/core/recap.py`
- Test: `tests/test_recap.py` (real git in `tmp_path`)

**Interfaces:**
- Consumes: Task 1 `read_note`, `Entry`; Task 2 `toplevel`, `state`, `GitState`; snapshot `Group`, `PaneRow`.
- Produces:
  - `dirs_for(group: Group, row: PaneRow | None) -> list[Path] | str`:
    - row → `[row.cwd]`;
    - a `config`/`git` group → `[group.key, *pane cwds]`;
    - waiting/other header → `"Select a project or a pane."`;
    - a row with no cwd → `"This pane has no directory."`
    - `None` cwds are dropped and order is kept.
  - `@dataclass(frozen=True) class Note: top: Path; entry: Entry | None` (`entry` = the latest; `None` when there's no note)
  - `notes(dirs: Sequence[Path], runner=subprocess.run) -> list[Note]`: each dir → `toplevel` or itself; dedupe. Places with a note come first, newest first; those without follow, in input order. Never empty for a non-empty `dirs`.
  - `since(note: Note, runner=subprocess.run) -> GitState | None`: `state(note.top, entry time, 20)`, or `since=None` when there's no entry.
  - `ago(seconds: float) -> str`:
    - `< 60` → `"just now"`, including negative values (clock skew);
    - otherwise `"1 minute ago"`, `"N minutes ago"`, `"N hours ago"`, `"N days ago"`, with singular forms.
- Entry time → timestamp via `entry.when.timestamp()` (local, naive).

- [ ] **Step 1: Failing tests:**
  - `test_dirs_for_*` (pane, project, header, no cwd).
  - `test_worktree_notes_listed_newest_first`: main checkout note dated 10-20, worktree note dated 10-24 → the worktree first.
  - `test_same_top_once`: two panes in one repo.
  - `test_no_note_place_listed_last`.
  - `test_missing_cwd_is_skipped_gracefully`: a nonexistent path ends up as `Note(path, None)` and nothing raises.
  - `test_not_a_repo_since_is_none`.
  - `test_since_counts_commits_after_entry`.
  - `test_ago_forms`.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(core): recap data: notes across worktrees, git since the checkpoint (#64)`

### Task 7: Deck: `c`, `r`, `R`, confirm dialog, recap screen

**Files:**
- Create: `src/cardak/ui/confirm.py` (`ConfirmScreen`), `src/cardak/ui/recap.py` (`RecapScreen`)
- Modify: `src/cardak/ui/app.py` (bindings, `DECK_ACTIONS`, `__init__` params, `action_checkpoint`, `action_recap`)
- Modify: `src/cardak/cli.py` `_deck` (pass `send=lambda p, t: wezterm.send_text(p, t)` and `skills_status=_skills_hint`)
- Test: `tests/test_app.py`, `tests/test_recap_screen.py`

**Interfaces:**
- Consumes: Tasks 3, 5, 6.
- Produces:
  - `DeckApp(..., send: Callable[[int, str], None] | None = None, skills_status: Callable[[], str | None] | None = None)`
  - `ConfirmScreen(ModalScreen[bool])(question: str, note: str)`: Enter → `True`, Esc → `False`. Texts go in as `Text`.
  - `RecapScreen(Screen[None])(title: str, dirs: Sequence[Path], now: float)`:
    - loads `recap.notes` and then `recap.since` in a thread worker, showing `Loading…` until then;
    - `#note` is a `Markdown` of the entry body;
    - `#head` is a `Text` line: `{dir display} · {branch or "no branch"} · checkpoint {ago}`, or with no note: `"No checkpoint yet. Press c on an agent pane to write one."`;
    - `#since` is a `Text`: `Since then: N commits` plus up to 20 lines `{sha} {subject}`, then `{changed} changed, {untracked} untracked`, or `Not a git repository.`;
    - `#others` is an `OptionList` of the other notes (`{dir} · {branch} · {ago}`, or `{dir} · no checkpoint`), shown only when there are any; Enter on one re-shows the screen for it, reusing the worker;
    - Esc pops.
- Bindings:
  - `c` → `checkpoint` "Checkpoint"; `r` → `recap` "Recap"; `R` → `refresh` "Refresh" with `show=False`.
  - Add `checkpoint` and `recap` to `DECK_ACTIONS`.
  - Existing tests that call `action_refresh` directly are unaffected; tests that press `"r"` for refresh change to `"R"`.
- `action_checkpoint`:
  1. `send is None` → nothing.
  2. `skills_status()` returns a hint → warn with it and don't send (ruling: an uninstalled `/checkpoint` would only fail in Claude).
  3. Otherwise run `checkpoint.target`; a str → warning.
  4. Otherwise push `ConfirmScreen` with the Global Constraints copy, project = `row.agent.project`.
  5. On `True`, a thread worker calls `send(pane_id, COMMAND)`. Success → `Sent /checkpoint to pane {id}.` and a refresh. `WeztermUnavailable` or any other `Exception` → error `Couldn't send: {e}`.
- `action_recap`: `recap.dirs_for` → str warning, or push `RecapScreen(group.name, dirs, time.time())`.

- [ ] **Step 1: Failing tests** (`StubSource`, recorded `send` list):
  - `test_c_confirm_sends_command`.
  - `test_c_escape_sends_nothing`.
  - `test_c_refused_working_shows_reason`.
  - `test_c_skills_missing_hint_no_send`.
  - `test_c_send_error_notified`.
  - `test_R_refreshes` and `test_r_opens_recap`.
  - `test_recap_screen_markup_safe`: a note body and branch with `[/x]`, a commit subject `[b]x`. The screen renders, and the text appears literally.
  - `test_recap_no_note_message`.
  - `test_recap_others_switch`.
- [ ] **Step 2: Run** `uv run pytest tests/test_app.py tests/test_recap_screen.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full suite plus `ruff check`, `ruff format --check` and `pyright`. Expected: all clean.
- [ ] **Step 5: Commit** `feat(ui): checkpoint key, confirm dialog and recap view (#64)`

### Task 8: Docs, live check, architecture

**Files:**
- Modify:
  - `AGENTS.md`: the layout line (skills move to `src/cardak/claude/skills/`; new modules in core/collectors/ui).
  - `AGENTS.md`: files-table rows for `<dir>/.cardak/handoff.md` (written by `/checkpoint`, read by the deck and `/where-were-we`) and `~/.claude/skills/{checkpoint,where-were-we}/SKILL.md` (`cardak skills install` / `uninstall`, after asking).
  - `AGENTS.md`: a "Checkpoint & recap (M6)" section with the measured `send-text` behaviour, the `current_tool == AskUserQuestion` rule, and the fact that the deck is read-only except for this one confirmed send.
  - `README.md`: a "Checkpoint and recap" section.
  - `CHANGELOG.md`: Unreleased → Added, about 3 lines for users.
  - `PLAN.md`: §7 drops `notes_file`; §9 says per-worktree notes, recap local only, no spawned pane; §11 ticks M6.

- [ ] **Step 1: Live check (isolated).**
  - Use a short scratch dir (Unix socket paths have a length limit) with `XDG_RUNTIME_DIR` and `WEZTERM_CONFIG_FILE` pointing into it, and `wezterm-mux-server --daemonize`.
  - Spawn a byte probe in a pane, and run the deck's own `send_text` against it through `WEZTERM_UNIX_SOCKET`. Expected: `b'/checkpoint\r'`.
  - Run `cardak skills install --dir <scratch>` and then `status`, plus `HOME=<scratch> cardak skills status`.
  - Stop the mux server by its pidfile only. Never `pkill` by pattern.
- [ ] **Step 2: Architecture.** `enola` snapshot and diff against the 0.8.0 pin: no cycles, no layer crossings. Split real outliers; record accepted ones in AGENTS.md's enola section under "M6".
- [ ] **Step 3: Write the docs.**
- [ ] **Step 4: Run** the full gate: `uv run pytest --cov --cov-branch --cov-fail-under=80`, `ruff check`, `ruff format --check`, `pyright`. Expected: green.
- [ ] **Step 5: Commit** `docs: checkpoint and recap in AGENTS.md, README, PLAN and the changelog (#64)`
