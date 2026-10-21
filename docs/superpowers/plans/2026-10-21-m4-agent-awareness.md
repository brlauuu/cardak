# M4: Agent Awareness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show in the deck which Claude Code session is waiting on the user and why, and
ping once (desktop notification and sound) when a session starts waiting.

**Architecture:** Claude Code hooks run `cardak hook`. It parses the payload
(`collectors/hooks.py`), applies a pure state transition (`core/agents.py`) inside a SQLite
transaction (`core/store.py`), and may spawn a detached notifier (`core/alerts.py`). The
deck reads the store on every poll, decides "seen" from jumps and the focus history that
`cardak.lua` v3 writes, and renders status dots, a "Waiting on you" group and a detail
panel. There is no daemon.

**Tech Stack:** Python 3.13, uv, Textual, sqlite3 (stdlib), WezTerm Lua (tested via lupa),
`notify-send`, `pw-play`/`paplay`.

**Spec:** `docs/superpowers/specs/2026-10-21-m4-agent-awareness-design.md`. Read it first.
It is the source of truth for every transition and copy string below.

## Global Constraints

- Layering: `cli → {ui, wezterm, claude} → core → collectors`. `core` never imports `ui`,
  `cardak.wezterm` or `cardak.claude`. `collectors` and `claude/` import nothing from cardak.
- Only `collectors/{claude,hooks,transcripts}.py` know Claude Code formats. Only
  `claude/install.py` writes under `~/.claude/`.
- `cardak hook` **always exits 0 and prints nothing**. It must not import `textual` or `psutil`.
  Budget: < 150 ms wall time.
- Hooks are synchronous with `timeout: 5`. Events: `SessionStart`, `UserPromptSubmit`,
  `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `PermissionRequest`, `Notification`,
  `Stop`, `SessionEnd`.
- State lives in `$XDG_STATE_HOME/cardak/` (default `~/.local/state/cardak/`): `cardak.db`,
  `focus`, `hook.log`.
- Tests run against recorded fixtures, never the live machine. Live checks use an isolated
  WezTerm (`--always-new-process`, its own `gui-sock-<pid>`, scratch `XDG_*`) and must not
  change focus. **Tell the user before any step that shows notifications or plays sounds.**
- Never write the user's `~/.claude/settings.json`, `~/.config/wezterm` or
  `~/.config/cardak` during development. Use `claude --settings <scratch file>`.
- CI gate: `uv run ruff check`, `uv run ruff format --check`, `uv run pyright`,
  `uv run pytest --cov=cardak --cov-branch --cov-fail-under=80`.
- Commits: `feat(scope): … (#32)`, ending with the `Co-Authored-By` trailer.

## Review Focus

1. **A garbage, empty, or unknown-event payload on stdin.** `cardak hook` exits 0 silently
   and logs it, and the store is unchanged. Tests: Task 2 (parse returns `None`) and Task 7
   (`test_hook_garbage_is_silent`).
2. **The store is locked by a concurrent writer** (the deck marking seen while a hook
   writes). The writer waits up to `busy_timeout`. If it still fails, the hook logs it and
   exits 0. Test: Task 4 `test_concurrent_update_waits`.
3. **One permission prompt reported twice** (`PermissionRequest`, then a
   `permission_prompt` notification, with a jump in between): exactly one ping. Test: Task 3
   `test_permission_prompt_after_request_never_pings_again`.
4. **The same pane in the waiting group and its project group.** Cursor restore, collapse
   memory and Enter work on both, with no node-id collisions. Test: Task 10
   `test_waiting_rows_have_their_own_node_ids`.
5. **`settings.json` with the user's own hooks on the same events, a symlinked file, or
   invalid JSON.** Their hooks survive install and uninstall byte-for-byte, the link
   survives, and invalid JSON aborts without writing. Tests: Task 8.

---

### Task 1: Record hook and transcript fixtures

**Files:**
- Create: `spikes/04_record_hooks.py` (PEP 723, no deps), `tests/fixtures/hooks/*.json`,
  `tests/fixtures/transcripts/basic.jsonl`
- Modify: `AGENTS.md` ("Claude Code (measured in M0)" → add an M4 section)

**Interfaces:**
- Produces: one sanitized fixture per event, named `<event>[-<variant>].json`. At least:
  `session_start.json`, `user_prompt_submit.json`, `pre_tool_use-bash.json`,
  `pre_tool_use-ask_user_question.json`, `post_tool_use-edit.json`,
  `post_tool_use_failure.json`, `permission_request.json`,
  `notification-permission_prompt.json`, `notification-idle_prompt.json`,
  `stop-question.json`, `stop-finished.json`, `session_end.json`. Each file is
  `{"payload": {...}, "env": {"WEZTERM_PANE": "<present|absent>"}}`.

- [ ] **Step 1: Write the recorder.** `spikes/04_record_hooks.py <outdir>` reads stdin and
  writes `<outdir>/<n>-<hook_event_name>.json` holding the payload and whether
  `WEZTERM_PANE` was set. Write a scratch settings file that registers it for every event
  in Global Constraints, plus `SubagentStop`.
- [ ] **Step 2: Record.** Tell the user first that this starts a real `claude` session
  in an isolated WezTerm. In a scratch git repo, start
  `claude --settings <scratch settings>` there. Drive it with `wezterm cli send-text`
  (its own socket) through:
  - a prompt that runs `ls` (permission prompt → approve);
  - a prompt that edits a file;
  - a prompt whose answer ends in a question;
  - a prompt that triggers `AskUserQuestion` ("ask me which of two options to use");
  - waiting 60 s for `idle_prompt`;
  - `/clear` (note whether `~/.claude/sessions/<pid>.json` changes `sessionId`);
  - exit.
  Copy the session's transcript JSONL.
- [ ] **Step 3: Sanitize and commit the fixtures.** Replace the home path with
  `/home/user`, the repo with `/home/user/repos/demo`, and session ids with fixed UUIDs.
  Keep every key.
- [ ] **Step 4: Record what you measured in AGENTS.md.**
  - Whether `WEZTERM_PANE` reaches hooks.
  - What `/clear` does to the session id and to `sessions/<pid>.json`.
  - The order of `PreToolUse` / `PermissionRequest` / `Notification` / `PostToolUse`
    around an approval.
  - Whether `Stop.last_assistant_message` is present.
  - The transcript line types seen.
  If a payload contradicts the spec's field names, stop and report it before Task 2.
- [ ] **Step 5: Commit** `test(fixtures): record Claude Code hook payloads and a transcript (#32)`.

### Task 2: Parse hook payloads (`collectors/hooks.py`)

**Files:** Create `src/cardak/collectors/hooks.py`, `tests/test_hooks.py`.

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True)
  class HookEvent:
      name: str                       # hook_event_name, verbatim
      session_id: str
      cwd: Path | None = None
      transcript_path: Path | None = None
      source: str | None = None       # SessionStart
      prompt: str | None = None       # UserPromptSubmit
      tool_name: str | None = None
      tool_input: Mapping[str, object] = field(default_factory=dict)
      notification_type: str | None = None
      message: str | None = None      # Notification
      last_assistant_message: str | None = None   # Stop
      reason: str | None = None       # SessionEnd
  def parse(text: str) -> HookEvent | None
  ```

- [ ] **Step 1: Write the failing tests.** Load each fixture and assert its key fields:
  - `test_parse_pre_tool_use_bash`: `name == "PreToolUse"`, `tool_name == "Bash"`,
    `"command" in tool_input`.
  - `test_parse_stop_has_last_message`.
  - `test_parse_notification_type`.
  - `test_parse_rejects`: parametrized over `""`, `"not json"`, `"[]"`,
    `'{"hook_event_name": "Stop"}'` (no session_id) and `'{"session_id": 3}'`. Each gives
    `None`.
  - `test_parse_tolerates_wrong_types`: `tool_input` as a string gives `{}`, and `cwd` as a
    number gives `None`.
- [ ] **Step 2: Run** `uv run pytest tests/test_hooks.py -v`. Expect FAIL (module missing).
- [ ] **Step 3: Implement `parse`.** Use `json.loads` and type-check every field. A
  non-dict payload, or a missing `hook_event_name` or string `session_id`, gives `None`.
  Never raise.
- [ ] **Step 4: Run.** Expect PASS.
- [ ] **Step 5: Commit** `feat(collectors): parse Claude Code hook payloads (#32)`.

### Task 3: Session state machine (`core/agents.py`)

**Files:** Create `src/cardak/core/agents.py`, `tests/test_agents.py`.

**Interfaces:**
- Consumes: `HookEvent` (Task 2).
- Produces:
  ```python
  Status = Literal["working", "waiting", "idle"]
  WaitKind = Literal["permission", "question", "finished"]
  KIND_LABELS: dict[WaitKind, str] = {"permission": "Approve a tool",
                                      "question": "Your answer", "finished": "Turn finished"}
  @dataclass(frozen=True)
  class SessionState:
      session_id: str
      pane_id: int | None = None
      cwd: Path | None = None
      transcript_path: Path | None = None
      status: Status = "idle"
      kind: WaitKind | None = None
      status_since: float = 0.0
      reason: str | None = None
      last_prompt: str | None = None
      last_prompt_at: float | None = None
      last_message: str | None = None
      current_tool: str | None = None
      turn_edits: int = 0
      turn_reads: int = 0
      turn_commands: int = 0
      turn_other: int = 0
      turn_files: tuple[str, ...] = ()
      permission_requested: bool = False   # a PermissionRequest since the last PreToolUse
      seen_at: float | None = None
      started_at: float = 0.0
      updated_at: float = 0.0
      ended_at: float | None = None
  @dataclass(frozen=True)
  class Ping:
      session_id: str
      pane_id: int | None
      cwd: Path | None
      kind: WaitKind
      reason: str
  @dataclass(frozen=True)
  class FocusChange:
      pane_id: int | None    # None: no WezTerm window focused
      at: float              # seconds since the epoch
  def apply(state: SessionState | None, event: HookEvent, pane_id: int | None, now: float
            ) -> tuple[SessionState, Ping | None]
  def mark_seen(state: SessionState, now: float) -> SessionState
  def seen_by_focus(state: SessionState, history: Sequence[FocusChange], now: float,
                    dwell: float = 1.0) -> bool
  def tool_summary(tool_name: str, tool_input: Mapping[str, object]) -> str
  def ends_with_question(text: str) -> bool
  ```

- [ ] **Step 1: Write the failing tests.** Build events with `HookEvent(...)` directly.
  - `test_transitions`: a table over the spec's *Transitions* table, one case per row,
    plus each `Notification` type. Assert `status`, `kind` and `reason` after each.
  - `test_ping_only_on_entering_waiting`: idle → `Stop` pings. A second `Stop` while
    waiting doesn't. `UserPromptSubmit` then `Stop` pings again.
  - `test_permission_prompt_after_request_never_pings_again`: `PreToolUse` →
    `PermissionRequest` (ping) → `mark_seen` → `Notification(permission_prompt)` gives no
    ping, and the status stays `working`.
  - `test_permission_prompt_without_request_pings`: `PreToolUse` →
    `Notification(permission_prompt)` gives `waiting`/`permission` and a ping.
  - `test_user_prompt_resets_turn`: the counters are 0, `turn_files == ()`, and
    `last_prompt` is set.
  - `test_post_tool_use_counts`: Edit, Write, MultiEdit and NotebookEdit count as edits and
    add `tool_input["file_path"]` (deduplicated, ≤ 50). Read, Grep, Glob, LS, WebFetch and
    WebSearch count as reads. Bash counts as a command. Anything else counts as other.
  - `test_ask_user_question_waits`: `PreToolUse` with `tool_name="AskUserQuestion"` gives
    `waiting`/`question`, with the first question's text as the reason.
  - `test_stop_kind`: a message ending `"…separate?"` gives `question`, `"Done."` gives
    `finished`, and `"Done?  \n"` gives `question`.
  - `test_mark_seen`: question/finished → idle; permission → working, with
    `current_tool == reason`; non-waiting is unchanged. `seen_at` is set.
  - `test_session_end_sets_ended_at`.
  - `test_seen_by_focus`: waiting since t=100.
    - `[(5, 99), (7, 100.5), (None, 101.2)]` with state pane 7 gives False (0.7 s).
    - `[(7, 100.5), (3, 102)]` gives True.
    - `[(7, 100.5)]` at now=101.6 gives True (open interval).
    - Focus that started before `status_since` counts only from `status_since`.
  - `test_tool_summary`:
    - `("Bash", {"command": "uv run pytest\nmore"})` → `"Bash: uv run pytest"`.
    - `("Edit", {"file_path": "/r/a.py"})` → `"Edit /r/a.py"`.
    - `("Grep", {"pattern": "foo"})` → `"Grep foo"`.
    - `("mcp__github__create_issue", {})` → `"github: create_issue"`.
    - Unknown with no input → the tool name.
    - Every summary is ≤ 80 chars, ending in `…` when cut.
- [ ] **Step 2: Run** `uv run pytest tests/test_agents.py -v`. Expect FAIL.
- [ ] **Step 3: Implement.** `apply` with `state=None` starts a fresh
  `SessionState(session_id, started_at=now)`. Every event updates `updated_at`, and
  `pane_id`/`cwd`/`transcript_path` when given. `status_since` changes only when `status`
  or `kind` changes. Stored prompts and messages are kept whole.
- [ ] **Step 4: Run.** Expect PASS.
- [ ] **Step 5: Commit** `feat(core): agent session state machine (#32)`.

### Task 4: SQLite store (`core/store.py`)

**Files:** Create `src/cardak/core/store.py`, `tests/test_store.py`. Modify
`src/cardak/core/config.py` (add `state_dir`).

**Interfaces:**
- Consumes: `SessionState` (Task 3).
- Produces:
  ```python
  # core/config.py
  def state_dir(env: Mapping[str, str] | None = None) -> Path   # $XDG_STATE_HOME/cardak or ~/.local/state/cardak
  # core/store.py
  SCHEMA_VERSION = 1
  def db_path(env: Mapping[str, str] | None = None) -> Path      # state_dir(env) / "cardak.db"
  class Store:
      def __init__(self, path: Path) -> None
      def update(self, session_id: str,
                 fn: Callable[[SessionState | None], tuple[SessionState, T]]) -> T
      def record_event(self, session_id: str, event: str, payload: str, now: float) -> None
      def live_sessions(self) -> list[SessionState]          # ended_at IS NULL
      def get(self, session_id: str) -> SessionState | None
      def mark_seen(self, session_id: str, now: float) -> None   # update() with agents.mark_seen
      def prune(self, now: float) -> None
      def close(self) -> None
  ```

- [ ] **Step 1: Write the failing tests** (all with `tmp_path`):
  - `test_round_trip`: every `SessionState` field survives `update` → `get`, including
    `turn_files` and `None`s.
  - `test_creates_parent_dir_and_schema_version`: `PRAGMA user_version == 1` and
    `journal_mode == "wal"`.
  - `test_live_sessions_skips_ended`.
  - `test_prune`: ended sessions older than 7 days go, recent ones stay. Events older than
    7 days go, and at most 10 000 events are kept.
  - `test_concurrent_update_waits`: a second `Store` on the same file is
    blocked by an open `BEGIN IMMEDIATE` in the first. The second's `update` succeeds once
    the first commits within 0.5 s (use a thread).
  - `test_newer_schema_is_refused`: `user_version = 2` raises `StoreError`.
- [ ] **Step 2: Run.** Expect FAIL.
- [ ] **Step 3: Implement.**
  - Connect with `sqlite3.connect(path, timeout=2.0, isolation_level=None)`, then set WAL.
  - `update` runs `BEGIN IMMEDIATE`, then reads, calls `fn`, upserts and commits, and
    rolls back on exception.
  - Columns follow the spec's schema (v1) plus `permission_requested`. `turn_files` is
    stored as JSON.
  - `StoreError(Exception)` wraps `sqlite3.Error`.
- [ ] **Step 4: Run.** Expect PASS.
- [ ] **Step 5: Commit** `feat(core): SQLite store for agent sessions (#32)`.

### Task 5: Notify config, sound pack, alerts (`core/alerts.py`)

**Files:**
- Create: `scripts/make_sounds.py`, `src/cardak/sounds/__init__.py`,
  `src/cardak/sounds/*.wav`, `src/cardak/core/alerts.py`, `tests/test_alerts.py`
- Modify: `src/cardak/core/config.py` (`[notify]`), `tests/test_config.py`

**Interfaces:**
- Consumes: `Ping`, `KIND_LABELS` (Task 3).
- Produces:
  ```python
  # core/config.py
  @dataclass(frozen=True)
  class NotifySettings:
      desktop: bool = True
      sound: str = "random"           # "random" | "off" | bundled name | file name in sounds_dir
      sounds_dir: Path | None = None
  # Config gains: notify: NotifySettings = NotifySettings()
  # core/alerts.py
  SOUND_EXTS = (".wav", ".oga", ".ogg")
  Spawn = Callable[[list[str]], None]
  def bundled_sounds() -> dict[str, Path]          # stem -> path, from importlib.resources
  def sound_pool(settings: NotifySettings, bundled: Mapping[str, Path]) -> dict[str, Path]
  def choose_sound(settings: NotifySettings, bundled: Mapping[str, Path],
                   rng: random.Random) -> Path | None
  def check(settings: NotifySettings, bundled: Mapping[str, Path]) -> list[str]   # deck warnings
  def notification_argv(project: str, ping: Ping) -> list[str]
  def player_argv(sound: Path, which: Callable[[str], str | None] = shutil.which) -> list[str] | None
  def spawn_detached(argv: list[str]) -> None
  def alert(project: str, ping: Ping, settings: NotifySettings, *, spawn: Spawn = spawn_detached,
            rng: random.Random | None = None, which: Callable[[str], str | None] = shutil.which) -> None
  ```

- [ ] **Step 1: Generate the sound pack.** `scripts/make_sounds.py` uses only `wave`,
  `math` and `struct`, and writes 5 mono 16-bit 44.1 kHz WAVs, each under 1 s with a soft
  attack and an exponential decay: `chime`, `pluck`, `bell`, `blip`, `drop`. Run it, listen
  to them only if the user agrees, and commit the output. Check that `uv build` includes
  `cardak/sounds/*.wav` (inspect the wheel).
- [ ] **Step 2: Write the failing tests.**
  - `test_config.py::test_notify_defaults` and `::test_notify_parsed`
    (`sounds_dir` is expanded with `~`).
  - `test_config.py::test_notify_bad_types`: `[notify] desktop = "yes"` → `ConfigError`,
    and `notify = 3` → `ConfigError`.
  - `test_bundled_sounds_present`: the keys equal `{"chime", "pluck", "bell", "blip", "drop"}`.
  - `test_choose_sound`:
    - `off` → `None`.
    - A bundled name → that path.
    - A file name present in `sounds_dir` → that file.
    - `random` with a seeded rng picks from bundled plus user files.
    - An unknown name falls back to random.
  - `test_check_warns_on_unknown_sound_and_missing_dir`.
  - `test_notification_argv`: equals
    `["notify-send", "-a", "cardak", "notes · claude", "Your answer: Want me to merge…"]`.
    The reason is cut to 120 chars with `…`.
  - `test_player_argv`: `pw-play` is preferred, then `paplay`; `None` when neither exists.
  - `test_alert_spawns`: `desktop=False` and `sound="off"` → no spawn. Defaults → two
    argv lists. The spawn is recorded, not run.
  - `test_spawn_detached_swallows_missing_binary`.
- [ ] **Step 3: Run.** Expect FAIL.
- [ ] **Step 4: Implement.**
  - `spawn_detached` uses `subprocess.Popen(argv, stdin/stdout/stderr=DEVNULL,
    start_new_session=True, close_fds=True)` and ignores `OSError`.
  - The config's `[notify]` parsing mirrors `[defaults]`'s error style.
- [ ] **Step 5: Run.** Expect PASS.
- [ ] **Step 6: Commit** `feat(core): notification and sound alerts (#32)`.

### Task 6: Focus history (`cardak.lua` v3 + `core/focus.py`)

**Files:**
- Modify: `src/cardak/wezterm/cardak.lua`, `src/cardak/wezterm/generate.py`
  (`MODULE_VERSION = 3`), `tests/test_lua.py`
- Create: `src/cardak/core/focus.py`, `tests/test_focus.py`

**Interfaces:**
- Consumes: `FocusChange` (Task 3), `state_dir` (Task 4).
- Produces:
  ```python
  FOCUS_HEADER = "# cardak-focus: 1"
  HISTORY = 64
  def focus_path(env: Mapping[str, str] | None = None) -> Path   # state_dir(env) / "focus"
  def read_history(path: Path) -> list[FocusChange]             # [] on missing/garbled/other version
  def focused_pane(history: Sequence[FocusChange]) -> int | None
  ```
  The file format: line 1 is `FOCUS_HEADER`, then up to 64 lines `<pane_id|none> <unix_ms>`,
  oldest first.

- [ ] **Step 1: Write the failing Lua tests.** Extend the harness:
  - `os.getenv` (XDG_STATE_HOME pointing at `tmp_path`);
  - `window:is_focused()`;
  - `active_pane():pane_id()`;
  - a `fire('window-focus-changed', w, pane)`.

  Tests:
  - `test_focus_written_on_change`: focusing window A (pane 3) writes the header plus
    `3 <ms>`. A repeated `update-status` with no change doesn't rewrite.
  - `test_focus_none_when_unfocused`: a focus-changed event with no window focused appends
    `none <ms>`.
  - `test_history_capped_at_64`.
  - `test_history_seeded_from_existing_file`: the module loaded over an existing file keeps
    its lines.
  - `test_module_version_3`: the second line is `-- cardak-module: 3`.
- [ ] **Step 2: Write the failing Python tests** (`test_focus.py`):
  - The round trip of a file written in the format gives `FocusChange`s in seconds.
  - A garbled line is skipped.
  - A wrong header gives `[]`.
  - `focused_pane` of `[]` and of a final `none` is `None`.
- [ ] **Step 3: Run** `uv run pytest tests/test_lua.py tests/test_focus.py -v`. Expect FAIL.
- [ ] **Step 4: Implement the Lua side.** A `report_focus(window)` is called from the
  existing `update-status` handler and from a new `window-focus-changed` handler.
  - It computes `(any window focused, active pane id)`, and on change appends to the
    in-memory history (seeded from the file at load).
  - It writes `path .. '.tmp'` and then `os.rename`.
  - Everything is wrapped in `pcall`.
  - The path is `(os.getenv('XDG_STATE_HOME') or os.getenv('HOME') .. '/.local/state') ..
    '/cardak/focus'`. If the directory is missing, it skips the write (Python creates the
    directory in `Store`/`hook`).
- [ ] **Step 5: Implement `core/focus.py`.** Run both test files. Expect PASS.
- [ ] **Step 6: Commit** `feat(wezterm): cardak.lua v3 reports pane focus (#32)`.

### Task 7: The hook command (`core/hook.py`, `cardak hook`)

**Files:**
- Create: `src/cardak/core/hook.py`, `tests/test_hook.py`
- Modify: `src/cardak/cli.py` (lazy imports and `hook` subcommand), `tests/test_cli.py`

**Interfaces:**
- Consumes: `parse` (Task 2); `apply`, `Ping` (Task 3); `Store`, `db_path` (Task 4);
  `alert`, `Spawn` (Task 5); `read_history`, `focused_pane`, `focus_path` (Task 6);
  `config.load`, `config_path`.
- Produces:
  ```python
  def run(text: str, env: Mapping[str, str], now: float, *, store: Store,
          config_file: Path, focus_file: Path, spawn: Spawn = spawn_detached) -> None
  def project_name(cwd: Path | None, config: Config) -> str   # longest configured prefix, else cwd.name, else "claude"
  def main(stdin: TextIO, env: Mapping[str, str]) -> int        # always 0; logs exceptions to state_dir/hook.log
  LOG_MAX = 1_000_000
  ```

- [ ] **Step 1: Write the failing tests.**
  - `test_hook_records_and_transitions`: feed `stop-finished.json`'s payload with
    `WEZTERM_PANE=7`. The store row is `waiting`/`finished`, `pane_id == 7`, and there is
    one event row.
  - `test_hook_pings_once`: a fake spawn records argv. Two `Stop`s give one notify and one
    play.
  - `test_hook_no_ping_when_pane_focused`: the focus file's last line is `7 <ms>` → no spawn.
    `none <ms>` → spawn.
  - `test_hook_bad_config_still_pings`: a broken projects.toml gives the default settings,
    with the cwd basename as the project.
  - `test_project_name`: the longest prefix wins, falling back to the basename.
  - `test_hook_garbage_is_silent`: `main(io.StringIO("nope"), env)` returns 0, captures no
    stdout, and leaves the store empty.
  - `test_hook_exception_is_logged`: with `store` raising, `main` returns 0 and `hook.log`
    has a traceback. The log is truncated when over `LOG_MAX`.
  - `test_cli.py::test_hook_does_not_import_textual`: run in a subprocess
    `python -c "import sys, io; sys.stdin = io.StringIO('{}'); from cardak.cli import main;
    main(['hook']); assert 'textual' not in sys.modules and 'psutil' not in sys.modules"`
    with `XDG_STATE_HOME` set to `tmp_path`. The exit status is 0.
- [ ] **Step 2: Run.** Expect FAIL.
- [ ] **Step 3: Implement.**
  - Move every non-stdlib import in `cli.py` into the function that uses it.
  - `cardak hook` dispatches to `core.hook.main(sys.stdin, os.environ)` before anything
    else.
  - `run` parses the event, records the raw event, runs `store.update(sid, lambda s:
    agents.apply(s, ev, pane, now))`, and calls `prune` on `SessionStart`.
  - On a ping, unless `focused_pane(read_history(focus_file)) == pane`, it loads the
    config (a `ConfigError` gives `Config()`) and calls `alert`.
- [ ] **Step 4: Run.** Expect PASS. Then run the whole suite (the lazy imports must not
  break the existing CLI tests).
- [ ] **Step 5: Measure.** Use `hyperfine` if present, else a 20-run loop with `time`:
  pipe the `payload` of `tests/fixtures/hooks/stop-finished.json` into
  `.venv/bin/cardak hook`, with a scratch `XDG_STATE_HOME` and a scratch config
  (`CARDAK_CONFIG`) that sets `[notify] desktop = false, sound = "off"`. Record the median in AGENTS.md. If it's ≥ 150 ms, profile the imports
  (`python -X importtime`) and trim before continuing.
- [ ] **Step 6: Commit** `feat(cli): cardak hook records Claude Code events (#32)`.

### Task 8: Hook installer (`claude/install.py`, `cardak hooks …`)

**Files:**
- Create: `src/cardak/claude/__init__.py`, `src/cardak/claude/install.py`,
  `tests/test_claude_install.py`, `tests/fixtures/settings/*.json`
- Modify: `src/cardak/cli.py`, `tests/test_cli.py`

**Interfaces:**
- Produces:
  ```python
  EVENTS: tuple[str, ...]   # the nine in Global Constraints, in that order
  HookStatus = Literal["installed", "partial", "missing"]
  class SettingsError(Exception): ...
  def default_settings_path() -> Path                   # ~/.claude/settings.json
  def hook_command(exe: Path) -> str                    # shlex.quote(str(exe)) + " hook"
  def is_cardak(command: str) -> bool                   # shlex-split; basename(argv[0]) == "cardak" and argv[1:] == ["hook"]
  def with_hooks(settings: dict, command: str) -> dict  # pure; removes old cardak entries, adds one group per event
  def without_hooks(settings: dict) -> dict             # pure; drops emptied groups/events, and "hooks" if emptied
  def status(settings: dict, command: str) -> HookStatus
  def changes(before: dict, after: dict) -> list[str]   # human lines: "+ Stop", "- Stop (old path)"
  def load_settings(path: Path) -> dict                 # {} if missing; SettingsError on invalid JSON / non-object
  def save_settings(path: Path, data: dict, now: datetime) -> Path   # backup, atomic write through symlink; returns backup path
  def find_executable(argv0: str, which: Callable[[str], str | None] = shutil.which) -> Path | None
  ```
  The cardak entry added per event is
  `{"hooks": [{"type": "command", "command": <command>, "timeout": 5}]}`, with no `matcher`.
  JSON is written with `indent=2` and a trailing newline.

- [ ] **Step 1: Write the fixtures.** `empty.json` (`{}`), `user_hooks.json` (the user's
  own `Stop` and `PreToolUse` hooks with matchers, plus unrelated keys `model` and
  `permissions`), `old_cardak.json` (cardak at `/old/bin/cardak`), `invalid.json`.
- [ ] **Step 2: Write the failing tests.**
  - `test_install_fresh`: all nine events present, and `status == "installed"`.
  - `test_install_is_idempotent`: `with_hooks(with_hooks(s)) == with_hooks(s)`, and the
    second `cardak hooks install --yes` writes nothing and prints `already installed`.
  - `test_install_keeps_user_hooks`: the user's groups are unchanged and still come
    first, and unrelated keys are identical.
  - `test_install_replaces_old_path`.
  - `test_uninstall_round_trip`: `without_hooks(with_hooks(user)) == user`.
  - `test_status_partial`.
  - `test_is_cardak`: true for `/x/cardak hook` and `'/a b/cardak' hook`. False for
    `cardak-other hook`, `/x/cardak hooks` and `echo cardak hook`.
  - `test_invalid_json_aborts`: `cardak hooks install --yes --settings invalid` exits 1,
    and the file is unchanged with no backup.
  - `test_save_through_symlink_keeps_link_and_mode`: the link still links, mode 0600 is
    kept, and a backup named `settings.json.cardak-backup-20261021-120000` exists.
  - `test_install_asks`: input `n` → no write, exit 0, and the changes are printed. Input
    `y` → written.
  - `test_find_executable`: an `argv0` named `cardak` → its absolute path (not resolved).
    Otherwise `which`. Otherwise `None` → `hooks install` exits 1 with a message.
- [ ] **Step 3: Run.** Expect FAIL.
- [ ] **Step 4: Implement.**
  - The subcommands are `cardak hooks install [--yes] [--settings PATH]`,
    `cardak hooks uninstall [--yes] [--settings PATH]` and
    `cardak hooks status [--settings PATH]`.
  - The prompt is `Write <path>? [y/N] `, printing the `changes` lines and the backup path
    first.
  - `claude/install.py` imports nothing from cardak (copy the small atomic-write helper).
- [ ] **Step 5: Run.** Expect PASS.
- [ ] **Step 6: Commit** `feat(claude): cardak hooks install/uninstall/status (#32)`.

### Task 9: Agents in the snapshot (`core/snapshot.py`, `core/source.py`, `core/filter.py`)

**Files:**
- Modify: `src/cardak/core/snapshot.py`, `src/cardak/core/source.py`,
  `src/cardak/core/filter.py`, `src/cardak/cli.py`
- Test: `tests/test_snapshot.py`, `tests/test_source.py`, `tests/test_filter.py`

**Interfaces:**
- Consumes: `SessionState`, `seen_by_focus`, `FocusChange` (Task 3); `Store` (Task 4);
  `read_history` (Task 6); `bundled_sounds`, `check` (Task 5).
- Produces:
  ```python
  # snapshot.py
  GroupKind = Kind | Literal["waiting"]
  WAITING_NAME = "Waiting on you"
  @dataclass(frozen=True)
  class AgentView:
      session_id: str
      status: Status
      kind: WaitKind | None
      since: float
      reason: str | None
      current_tool: str | None
      project: str
  # PaneRow gains: is_claude: bool = False; agent: AgentView | None = None
  # Group.kind becomes GroupKind
  # DeckSnapshot gains: n_waiting: int = 0; n_projects/n_panes exclude the waiting group
  # build_snapshot gains: sessions: Sequence[SessionState] = ()
  # filter.py
  def waiting_only(snap: DeckSnapshot) -> DeckSnapshot
  # source.py: LiveSource gains ctor args
  #   store: AgentStore | None = None
  #   focus_history: Callable[[], Sequence[FocusChange]] = lambda: []
  #   clock: Callable[[], float] = time.time
  class AgentStore(Protocol):
      def live_sessions(self) -> list[SessionState]: ...
      def get(self, session_id: str) -> SessionState | None: ...
      def mark_seen(self, session_id: str, now: float) -> None: ...
  # LiveSource methods:
  def mark_seen_pane(self, pane_id: int) -> None
  def session(self, session_id: str) -> SessionState | None
  ```

- [ ] **Step 1: Write the failing tests.**
  - `test_snapshot.py::test_agent_joined_by_pane_id`: a Claude pane 7 with a session at
    `pane_id=7` gets `agent.status`. `is_claude` is True.
  - `::test_agent_joined_by_session_id_fallback`: a session with `pane_id=None` joins via
    `claude_lookup(pid).session_id`.
  - `::test_newest_session_wins_per_pane` (`/clear`: the larger `started_at` wins).
  - `::test_claude_without_record`: `is_claude=True`, `agent=None`, and it's not counted
    as waiting.
  - `::test_non_claude_pane_ignores_session_on_its_pane`.
  - `::test_waiting_group_first_oldest_first`: the group named `WAITING_NAME`, kind
    `"waiting"`, comes first. Its rows are sorted by `agent.since` ascending.
    `n_waiting == 2`, and `n_panes`/`n_projects` are unchanged by it.
  - `::test_no_waiting_group_when_none_wait`.
  - `test_filter.py::test_waiting_only`: only the waiting group remains.
    `::test_find_counts_exclude_waiting_group`.
  - `test_source.py::test_poll_marks_seen_by_focus`: a fake store has a waiting session on
    pane 7 since t=100. The focus history is `[(7, 100.2)]` and `clock=lambda: 102`. After
    the poll, `mark_seen` was called once.
  - `::test_mark_seen_pane`: marks only waiting sessions on that pane.
  - `::test_notify_warnings_reach_banner`: `sound = "nope"` → a config warning.
- [ ] **Step 2: Run.** Expect FAIL.
- [ ] **Step 3: Implement.**
  - `_label` keeps its text.
  - An agent row's `project` is its group's name.
  - `LiveSource._poll` evaluates `seen_by_focus` for each waiting session before building,
    and re-reads after any mark.
  - Store errors are caught and give a config warning `agent status unavailable: <e>`.
  - `cli.py` wires `Store(db_path())` and `lambda: read_history(focus_path())` into
    `LiveSource`.
- [ ] **Step 4: Run** the whole suite. Expect PASS.
- [ ] **Step 5: Commit** `feat(core): agent status in the deck snapshot (#32)`.

### Task 10: Deck UI — dots, waiting group, `w`, seen on jump

**Files:**
- Modify: `src/cardak/ui/app.py`, `src/cardak/ui/widgets.py`, `src/cardak/cli.py`
- Test: `tests/test_app.py`, `tests/test_widgets.py`

**Interfaces:**
- Consumes: Task 9's `AgentView`, `PaneRow.agent`/`is_claude`, `waiting_only`,
  `DeckSource.mark_seen_pane`.
- Produces:
  ```python
  # app.py: NodeData is replaced by
  @dataclass(frozen=True)
  class Node:
      group: Group
      row: PaneRow | None = None
      role: Literal["group", "pane", "reason"] = "group"
  def node_id(node: Node | None) -> str | None
      # "g:<kind>:<key>", "p:<pane>", "w:<pane>" (pane in the waiting group), "r:<pane>:<kind>"
  # DeckSource protocol gains: mark_seen_pane(pane_id) and session(session_id)
  # DeckApp gains ctor arg hooks_status: Callable[[], str | None] | None = None
  # widgets.py
  def age(seconds: float) -> str          # "<1m", "3m", "2h", "4d"
  def agent_dot(row: PaneRow) -> Text     # "● " dark_orange / green, "○ " grey50, "· " dim, "" for non-Claude
  def pane_label(r: PaneRow, now: float) -> Text     # dot prefix; suffix "waiting 3m" | current tool | "idle 2h"
  def reason_label(r: PaneRow) -> Text    # "↳ <reason>" in dark_orange
  def waiting_label(r: PaneRow, now: float) -> Text  # "● <project> · claude  12m  <reason>"
  ```

- [ ] **Step 1: Write the failing tests.**
  - `test_widgets.py::test_age` (59 → `<1m`, 180 → `3m`, 7200 → `2h`, 4×86400 → `4d`).
  - `::test_pane_label_states`: one case per status, plus no record (`· `) and a shell (no
    dot).
  - `::test_waiting_label`: equals `"● notes · claude  12m  Your answer: Want me…"`
    (plain text). The reason is prefixed with the kind label.
  - `test_app.py::test_waiting_rows_have_their_own_node_ids`: a snapshot where pane 7
    waits gives node ids `w:7`, `p:7` and `r:7:…`, all distinct. Enter on `w:7` and on
    `r:7:…` both jump to 7. Collapsing the waiting group and re-polling keeps it collapsed.
  - `::test_waiting_row_has_reason_line`: a waiting row in a project group is followed by
    its `↳` line, and idle/working rows have none.
  - `::test_w_toggles_waiting_only`.
  - `::test_header_counts_waiting`: the sub-title ends `· 2 waiting`.
  - `::test_jump_marks_seen`: the fake source's `mark_seen_pane(7)` is called after a
    successful jump, and not after a failed one.
  - `::test_hooks_hint_once`: `hooks_status` returning a string → one warning toast on
    mount, shown the same way as `theme_status` (this is the spec's "header hint").
  - Update the existing tests from tuple `NodeData` to `Node`.
- [ ] **Step 2: Run.** Expect FAIL.
- [ ] **Step 3: Implement.**
  - The waiting group's label is `Waiting on you` in bold dark_orange.
  - Labels need `now`, so `_rebuild` passes `time.time()`.
  - `cli.py` passes
    `hooks_status=lambda: None if status(load_settings(…), hook_command(exe)) == "installed"
    else "Run `cardak hooks install` for agent status"`. A `SettingsError` also gives the
    hint.
- [ ] **Step 4: Run** the whole suite. Expect PASS.
- [ ] **Step 5: Commit** `feat(ui): agent status dots and the waiting group (#32)`.

### Task 11: Detail panel and transcript view

**Files:**
- Create: `src/cardak/collectors/transcripts.py`, `src/cardak/ui/detail.py`,
  `tests/test_transcripts.py`, `tests/test_detail.py`
- Modify: `src/cardak/ui/app.py` (`i` binding)

**Interfaces:**
- Consumes: `SessionState`, `KIND_LABELS`, `tool_summary` (Task 3); `DeckSource.session`
  (Task 9); the transcript fixture (Task 1).
- Produces:
  ```python
  # collectors/transcripts.py
  @dataclass(frozen=True)
  class Entry:
      kind: Literal["user", "assistant", "tool", "thinking"]
      text: str = ""                      # user/assistant/thinking text; tool result for "tool"
      tool_name: str | None = None
      tool_input: Mapping[str, object] = field(default_factory=dict)
  def read_transcript(path: Path, limit: int = 200) -> tuple[list[Entry], int]   # (last `limit` entries, number earlier)
  # ui/detail.py
  def detail_text(state: SessionState, project: str, now: float) -> Text
  class DetailScreen(Screen[int | None]):       # dismisses with a pane id to jump to
      def __init__(self, state: SessionState, project: str) -> None
  class TranscriptScreen(Screen[None]):
      def __init__(self, path: Path, title: str) -> None
  ```

- [ ] **Step 1: Write the failing tests.**
  - `test_transcripts.py::test_reads_fixture`:
    - User and assistant texts appear in order.
    - A tool_use and its tool_result become one `tool` entry carrying the result text.
    - Thinking blocks become `thinking` entries.
    - Meta and summary lines are skipped.
  - `::test_limit`: `limit=2` returns the last 2 entries and the right number earlier.
  - `::test_garbled_lines_skipped`, and `::test_missing_file` gives `([], 0)`.
  - `test_detail.py::test_detail_text_permission`: the plain text contains
    `NEEDS  Approve a tool`, `Bash: uv run pytest`, `YOU ASKED  (4m ago)`, the prompt,
    `THIS TURN  3 edits, 2 reads, 1 command` and `edited src/a.py`.
  - `::test_detail_text_idle_and_working`: `NEEDS  Nothing`, and `NEEDS  Working: Edit x`.
  - `::test_detail_trims_to_100_words`: a 300-word message shows ≤ 100 words plus `…`,
    with the end of the message kept (`…` first).
  - `::test_detail_screen_keys`: Enter dismisses with the pane id, `t` pushes
    `TranscriptScreen`, and Esc dismisses with `None`.
  - `test_app.py::test_i_opens_detail_on_claude_rows_only`.
- [ ] **Step 2: Run.** Expect FAIL.
- [ ] **Step 3: Implement.**
  - `TranscriptScreen` is a `VerticalScroll`. Text entries are `Static`s. Each tool is a
    collapsed `Collapsible(title=tool_summary(...))` holding its input JSON and result.
    Thinking is a collapsed `Collapsible(title="thinking")`.
  - A top `load earlier (N)` button re-reads with `limit + 200`. `r` reloads.
  - The transcript is read in a thread worker.
  - On a jump result from `DetailScreen`, the app calls `jump(pane_id)`.
- [ ] **Step 4: Run** the whole suite. Expect PASS.
- [ ] **Step 5: Commit** `feat(ui): agent detail panel and transcript view (#32)`.

### Task 12: Docs, architecture check, live check

**Files:** Modify `README.md`, `PLAN.md`, `AGENTS.md`, `CHANGELOG.md`.

- [ ] **Step 1: Docs.**
  - README: a "Claude Code status" section covering `cardak hooks install`, the trust
    dialog, `[notify]` with an example, the sound pack, re-running
    `cardak wezterm install` for focus (module 3), and `w`/`i`/`t`.
  - PLAN §5: hooks write SQLite directly until M5. §8: update the table to the spec's
    status model. §11: tick M4 and move "Daemon" to M5.
  - AGENTS.md:
    - the Layout (with `claude/`, `core/{agents,store,alerts,focus,hook}.py`, `sounds/`);
    - Layering (`claude/` imports nothing from cardak);
    - "One interface per external format" (the three collectors);
    - the hook contract and its measured latency;
    - the Themes table rows for `~/.local/state/cardak/{cardak.db,focus,hook.log}`, and
      `~/.claude/settings.json` (written only by `cardak hooks install|uninstall`, with
      a backup).
  - CHANGELOG `## Unreleased → Added`: user-facing lines for the hooks, the status dots
    and waiting group, the notification and sound, and the detail/transcript view.
- [ ] **Step 2: Gate.** Run the four CI commands. All are green, and coverage is ≥ 80%.
- [ ] **Step 3: enola.** Run `generate_snapshot` + `diff_snapshot` against the pinned
  v0.3.0 baseline.
  - Fix any cycle or layer violation, such as `core` importing `claude/`.
  - Judge new hotspots by absolute size, and record accepted coordinators in AGENTS.md
    "Architecture checks".
- [ ] **Step 4: Live check.** **Ask the user first: it shows real notifications and plays
  sounds.**
  - Use an isolated WezTerm with scratch `XDG_CONFIG_HOME`/`XDG_STATE_HOME`, and install
    the module v3 there.
  - Use a scratch `settings.json` made by
    `cardak hooks install --yes --settings <scratch>`, and run
    `claude --settings <scratch>` in a scratch repo.
  - Run the deck in another pane of the same isolated GUI. Verify:
    - the permission prompt → waiting with `Bash: …` plus one notification and sound;
    - jump → working;
    - a finished turn → waiting;
    - focusing the pane for > 1 s → idle;
    - `i` and `t` render.
  - Capture screenshots by `_NET_WM_PID`.
- [ ] **Step 5: Commit** `docs: M4 agent awareness (#32)`. Then re-pin enola
  (`enola baseline pin`) after merge, per AGENTS.md.
