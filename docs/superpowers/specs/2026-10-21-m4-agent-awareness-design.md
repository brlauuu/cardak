# M4: agent awareness — design

Issue: #32. Milestone M4 in `PLAN.md` §11, building on the Claude Code integration in §8.
An LLM-written summary of a waiting session is #30 and is **not** part of M4. M4 stores
everything #30 needs, so it can be added later without rework.

## Intent

The user runs several Claude Code sessions at once, in different WezTerm windows. The
question they keep answering by hand is **which session is waiting on me, and why**.
Today they have to visit each pane to find out, because a session can stop for a
permission prompt, a question, or simply because it finished its turn, and nothing tells
them which.

M4 answers that question in the deck, and it pings the user once when a session starts
waiting. It replaces peon-ping, which the user will uninstall.

## Outcome

- Every Claude pane in the deck has a status dot: **waiting** (orange `●`), **working**
  (green `●`) or **idle** (grey `○`). Shell panes are unchanged.
- A **"Waiting on you"** group is pinned at the top of the tree. It lists every waiting
  session, oldest first, as `● <project> · claude  12m  <reason>`. Enter on one jumps to
  the pane, like any other row. Waiting sessions also stay in their project group.
- In the project groups, a waiting row shows `waiting 3m` and a second line
  `↳ <reason>`. A working row shows the current tool (`Edit src/routes.py`). An idle row
  shows `idle 2h`. The header gains `· N waiting`.
- When a session turns waiting, there is **one desktop notification and one sound**,
  with no reminders. This happens whether or not the deck is open.
- `i` on a Claude row (in either group) opens a **detail panel**: what kind of input is
  needed, the ask itself, the user's last prompt, and what this turn did. In the panel,
  `t` opens the full transcript.
- `cardak hooks install` and `cardak hooks uninstall` manage cardak's entries in
  `~/.claude/settings.json`.

**Success criteria**
- With hooks installed, a session that hits a permission prompt shows as waiting in the
  deck within one refresh, with the tool and command as its reason. A notification and a
  sound fire once.
- A session that ends its turn shows as waiting, with its final message as the reason.
  Jumping to it from the deck turns it idle.
- Typing a prompt turns it working. Approving a tool turns it working once the user has
  seen the prompt (see *Seen*), since Claude Code sends no event on approval itself.
- `cardak hooks install` run twice leaves `settings.json` identical to the first run.
  `uninstall` restores the user's own hooks exactly.
- A `cardak hook` call returns in under 150 ms on the dev machine (measured, recorded in
  AGENTS.md). It never blocks or fails a Claude Code action.
- CI (ruff, pyright, pytest with ≥ 80% branch coverage) is green.

## Non-goals and anti-goals

**Not in M4:** an LLM summary (#30); monthly spend (#29); a background daemon (moved to M5,
where resource sampling needs one); reminders or repeat notifications; per-subagent status;
terminals other than WezTerm; driving or answering sessions from the deck (the deck stays
read-only, AGENTS.md).

**Anti-goals** (failures even if it "works"):
- A hook that slows Claude Code down noticeably, prints anything, or exits non-zero.
  Exit code 2 on `PreToolUse` would **block the tool**, and stdout can be injected into the
  conversation.
- A notification storm: more than one ping per transition into waiting.
- Touching the user's own hooks or any other key in `settings.json`, or writing it without
  asking.
- Sound or notification files that the public repo can't license.

## Status model

Status is per Claude Code session, keyed by `session_id`.

| Status | Meaning |
|---|---|
| `working` | Claude is doing something: after a prompt, or a tool starting or finishing. |
| `waiting` | It needs the user, and the user hasn't looked since it started needing them. |
| `idle` | It needs nothing new: a fresh session, or a waiting one the user has since seen. |

A waiting session has a **kind**:

| Kind | Label in the panel | Set by |
|---|---|---|
| `permission` | Approve a tool | `PermissionRequest`, or `Notification` with `notification_type = permission_prompt` |
| `question` | Your answer | `PreToolUse` for `AskUserQuestion`; `Stop` whose `last_assistant_message`, trimmed, ends with `?`; `Notification` `elicitation_dialog` / `agent_needs_input` |
| `finished` | Turn finished | `Stop` otherwise |

The final-`?` rule is a heuristic and will sometimes be wrong; that's accepted.

**Transitions** (one row per hook event; the hook reads `hook_event_name` from stdin):

| Event | Effect |
|---|---|
| `SessionStart` | Create or refresh the row; status `idle`. Store `cwd`, `transcript_path`, `source`. |
| `UserPromptSubmit` | `working`. Store `prompt` as `last_prompt`, reset the turn counters and file list. |
| `PreToolUse` | `working`; store the current tool and a short summary of its input. For `AskUserQuestion`, go to `waiting`/`question` with the question text instead. |
| `PostToolUse`, `PostToolUseFailure` | `working`. Count the tool (edit / read / command / other) and add edited file paths to the turn's file list. |
| `PermissionRequest` | `waiting`/`permission`, with the tool name and input summary as the reason. |
| `Notification` | `permission_prompt`: a fallback for `PermissionRequest`. It acts only if no `PermissionRequest` arrived since the last `PreToolUse`, whatever the current status, so one prompt never pings twice. Elicitation and `agent_needs_input`: `waiting`/`question` with `message`. `idle_prompt` and anything else: store `message`, no status change. |
| `Stop` | `waiting`, with kind `question` or `finished`. Store `last_assistant_message`. |
| `SubagentStop` | Ignored: the main session is still working. |
| `SessionEnd` | Mark the row ended. The deck stops showing it. |

**Seen.** A waiting session counts as seen when:
- the user jumps to its pane from the deck, or
- its pane was the focused pane of the focused WezTerm window for at least 1 s after it
  started waiting (see *Focus* below).

The **deck** decides "seen" and writes `seen_at`; the hook never does. It evaluates the
focus history at every poll, and at startup for everything that happened while it was
closed, so a quick visit between two polls still counts.

A seen `question` or `finished` wait becomes `idle`. A seen `permission` wait becomes
`working`, with the pending tool as its current tool. Claude Code sends no event when a
prompt is approved, and nearly every seen prompt is answered: a denial also makes Claude
continue, and its next event corrects the status. Any later event that means working takes
a session out of idle or waiting as usual.

**Sessions with no record.** A Claude pane with no row in the store shows a dim `·` and no
status. This covers sessions started before `cardak hooks install`, since Claude Code reads
hooks at session start, and machines without hooks. These panes are never counted as
waiting. When `cardak hooks status` would say "not installed", the deck header shows
`· run cardak hooks install for agent status` once per run.

**Ping rule.** The hook fires the notification and sound on every transition **into**
`waiting`, and never on waiting → waiting. Exception: no ping if that session's pane is
the focused pane of the focused window at that moment, because the user is already
looking at it. (A design call; see the end.)

## Architecture

```
Claude Code ── hook (stdin JSON) ──▶ cardak hook ──▶ core/agents (pure transition)
                                          │              │
                                          │              ▼
                                          │         core/store (SQLite)  ◀── deck reads every refresh,
                                          ▼                                  writes seen_at on jump
                                    core/alerts ─▶ notify-send, pw-play / paplay (detached)

cardak.lua (module v3) ── on focus change ──▶ $XDG_STATE_HOME/cardak/focus ◀── hook and deck read
```

There is **no daemon in M4**. Each hook call is a short process that reads the event,
updates SQLite, maybe spawns a notifier, and exits. PLAN §5's daemon arrives in M5.

### Modules

Layering stays `cli → {ui, wezterm, claude} → core → collectors`.

| Module | Role |
|---|---|
| `collectors/hooks.py` | Parse a hook payload into a typed event. The only code that knows payload field names. Defensive: unknown events and missing fields give `None` or defaults, never an exception. |
| `collectors/transcripts.py` | Read a transcript JSONL lazily, for the full transcript view only: user and assistant messages, tool calls with inputs and results, thinking blocks. |
| `collectors/claude.py` | Unchanged (`~/.claude/sessions/<pid>.json`). |
| `core/agents.py` | **Pure.** `apply(state, event, now) -> (new_state, ping: Ping | None)`, plus the input summaries (`Bash: <command>`, `Edit <path>`, …) and the `?` rule. Has the most tests. |
| `core/store.py` | SQLite at `$XDG_STATE_HOME/cardak/cardak.db` (default `~/.local/state/cardak/`). WAL mode, `busy_timeout` 2 s, schema version in `PRAGMA user_version`. Reads and writes the session rows; prunes ended rows older than 7 days on write. |
| `core/alerts.py` | Builds the notification text and spawns `notify-send` and the sound player **detached** (`start_new_session=True`, no waiting), so the hook returns at once. A missing tool is skipped silently. |
| `core/focus.py` | Reads the focus file. |
| `claude/install.py` | New subpackage, the Claude Code counterpart of `cardak/wezterm/`: installs and removes hooks in `settings.json`. Imports nothing from cardak. |
| `ui/detail.py` | The detail panel and the transcript view. |
| `cli.py` | `cardak hook`, `cardak hooks install|uninstall|status`. **Imports `ui` and Textual lazily**, so the hook path never loads Textual. |

AGENTS.md says all Claude Code parsing lives behind one module. M4 makes that "the
`collectors/{claude,hooks,transcripts}.py` modules", as the AGENTS.md layout already
anticipates. The installer in `claude/` is the one other place that touches
`~/.claude/`, as `cardak/wezterm/` is for WezTerm. Update AGENTS.md accordingly.

### Joining a session to a pane

1. **`WEZTERM_PANE`.** A hook inherits the environment of the `claude` process, which runs
   in a WezTerm pane, so the hook can store the pane id directly. This is the primary key
   for the join. Verify it live (it is not documented by Claude Code).
2. Fallback: the existing `claude` pid → `~/.claude/sessions/<pid>.json` → `sessionId`
   lookup in the snapshot.

The deck shows only sessions that join to a live pane whose process tree has a `claude`
process, so stale rows (a crashed session with no `SessionEnd`) never appear.

`/clear` ends one session and starts another in the same pane. Both have the same pane id,
so the pane shows the newest non-ended session.

### Store schema (v1)

Follows PLAN §6's `agent_sessions`, extended for the panel and #30:

`agent_sessions(session_id PK, pane_id, cwd, transcript_path, status, kind, status_since,
reason, last_prompt, last_prompt_at, last_message, current_tool, turn_edits, turn_reads,
turn_commands, turn_other, turn_files_json, seen_at, started_at, updated_at, ended_at)`

`events(ts, session_id, event, payload_json)` is the raw hook payload, kept for debugging
and for #30. It is pruned to 7 days, with a size cap (builder's choice, e.g. 10 000 rows).

`turn_files_json` lists edited paths only, deduplicated, in order, capped at 50. Prompts and
messages are stored in full. The panel trims to about 100 words.

### Hook process contract

- The command is the absolute path of the `cardak` executable plus `hook`, so it works
  without `PATH`. Hooks are **synchronous** (not `async`), which keeps events in order, with
  `timeout: 5`.
- It reads stdin, does its work, and **always exits 0 with no output**. Any exception goes
  to `$XDG_STATE_HOME/cardak/hook.log` (capped, e.g. rotate at 1 MB).
- Budget: under 150 ms wall time. It imports only `collectors.hooks`, `core.agents`,
  `core.store`, `core.alerts`, `core.focus` and config loading, not Textual, psutil or
  WezTerm calls.
- The project name for the notification comes from `projects.toml` by longest path prefix
  of `cwd`, falling back to the basename of `cwd`. There are no git calls in the hook.

## Notifications and sounds

- Desktop: `notify-send -a cardak "<project> · claude" "<label>: <reason, ≤ 120 chars>"`.
- Sound: one file per ping, played with `pw-play`, falling back to `paplay`. If neither
  exists, there's no sound.
- **Bundled pack:** 4–6 short tones (under 1 s, WAV), **generated by a checked-in script**
  (`scripts/make_sounds.py`, numpy-free, using `wave` + `math`), shipped as package data
  under `src/cardak/sounds/`. No third-party audio, so there are no licensing questions.
- Config, in `projects.toml`:

  ```toml
  [notify]
  desktop = true            # notify-send on → waiting
  sound = "random"          # "random" | "off" | a bundled name ("chime") | a file name in sounds_dir
  sounds_dir = "~/sounds/cardak"   # optional; .wav/.oga/.ogg files here join the pool
  ```

  `random` picks from bundled sounds plus `sounds_dir`. Unknown values give a deck config
  warning, and the hook falls back to `random`.

## Focus (`cardak.lua` module v3)

`cardak.lua` gains a focus reporter. On `window-focus-changed` and `update-status`, when
the pair (is a WezTerm window focused, its active pane id) differs from the last value written, it writes
a new line `<pane_id> <unix_ms>`, or `none <unix_ms>` when no WezTerm window is
focused, to `$XDG_STATE_HOME/cardak/focus` (default `~/.local/state/cardak/focus`). The
file keeps the **last 64 changes**, oldest first, so each pane's focus intervals can be
reconstructed after the fact. It is rewritten whole via temp-file-and-rename. The first
line is `# cardak-focus: 1`, as a format version. The hook reads only the last line, for
the ping rule. The deck reads the whole history, for "seen".

Bump `MODULE_VERSION` to 3. With an older module there is no focus file: "seen" then comes
only from deck jumps, and the ping-suppression rule never applies. The deck's existing
"re-run `cardak wezterm install`" message covers it.

## Deck changes

- `DeckSnapshot` gains `n_waiting` and a `waiting` tuple. `PaneRow` gains an optional
  `agent: AgentView` (status, kind, since, reason, current tool). `LiveSource` reads the
  store once per poll. `core/snapshot.py` stays pure: the store's rows are passed in.
- Tree: the "Waiting on you" group is first, present only when non-empty and expanded by
  default. Its rows refer to the same panes as the project rows, so **node ids must differ
  per group** (cursor restore and collapse memory are keyed by node id; check
  `node_id`/`_cursor_target`).
- `w` toggles "waiting only": the tree shows just the waiting group. It reuses the
  existing filter path. `i` opens the detail panel on a Claude row.
- A jump to a waiting session's pane writes `seen_at` (via `LiveSource`, under its lock),
  and so does a poll that finds a focus interval of at least 1 s after `status_since`.

### Detail panel (`ui/detail.py`)

A screen pushed over the deck, laid out as the approved mockup:

```
● cardak · claude  waiting 3m  · session "m4 hooks"
NEEDS      Approve a tool
  Bash: uv run pytest tests/core -x
YOU ASKED  (4m ago)
  "fix the symlink matching in the resolver (#14)"
THIS TURN  3 edits, 2 reads, 1 command
  edited src/cardak/core/resolver.py
enter: jump to pane   t: full transcript   esc: back
```

For a working or idle session, NEEDS reads `Nothing` (idle) or `Working: <current tool>`.
The block is all quotes and counts, with nothing generated. #30 will put a summary above it.

**Transcript view:** `t` in the panel. A scrollable list of the session's messages from
`transcript_path`, read when opened (and re-read on `r`). User and assistant text is shown
in full. Each tool call is a collapsed `Collapsible` titled `<tool>: <summary>`, with input
and result inside. Thinking blocks are collapsed. For very long transcripts, show the last
200 entries with a "load earlier" row. Esc goes back.

## `cardak hooks install | uninstall | status`

- Target: `~/.claude/settings.json` (create it if missing, as `{}`). Writes are atomic,
  through symlinks, and keep the mode, like the M3 writers.
- **Ownership:** a hook entry is cardak's when its `command` ends with ` hook` and its
  executable's basename is `cardak`. Install removes any old cardak entries (e.g. an old
  path) and adds one per event under its own matcher group: `SessionStart`,
  `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `PostToolUseFailure`,
  `PermissionRequest`, `Notification`, `Stop`, `SessionEnd`. It never edits another entry
  or key.
- **Idempotent:** if the result equals the current file, it does nothing and says so.
- **Backup** before any write: `settings.json.cardak-backup-<YYYYmmdd-HHMMSS>` next to it.
- **Asks first:** it prints which events will be added or removed and the backup path,
  then asks `Write ~/.claude/settings.json? [y/N]`. `--yes` skips the question.
- `uninstall` removes only cardak's entries and drops matcher groups or event keys left
  empty by that, and nothing else. `status` reports installed / partial / not installed,
  and the command path.
- Invalid JSON in `settings.json` aborts with a message and changes nothing.
- Claude Code holds back hooks until the workspace trust dialog is accepted. Mention it in
  the README.

## Testing

- **Fixtures first** (AGENTS.md): record real payloads for every event above, plus a
  transcript, into `tests/fixtures/hooks/` and `tests/fixtures/transcripts/`, sanitized
  (no personal paths or content).
  - Record them with a throwaway session started as
    `claude --settings <scratch settings with a recorder hook>` in a scratch directory.
    This never touches the user's `~/.claude/settings.json`.
  - Also record whether `WEZTERM_PANE` reaches the hook, and what `/clear` does to
    `sessions/<pid>.json`.
- `core/agents.py`: a table-driven test over every transition and the ping rule.
- `core/store.py`: against a temp DB, including schema version and pruning.
- `claude/install.py`: against fixture settings files, covering a fresh install,
  re-install (byte-identical), an old cardak path, other users' hooks preserved, uninstall
  round-trip, invalid JSON, and a symlinked file.
- `core/alerts.py`: the spawn is injected, so tests assert argv, not real sounds.
- CLI: `cardak hook` with a fixture on stdin updates a temp DB, prints nothing and exits 0,
  also on garbage input. Add a test that the hook path doesn't import `textual`
  (`sys.modules` check in a subprocess).
- UI: Textual pilot tests for the waiting group, distinct node ids, `w`, `i`, and seen on
  jump.
- Lua: extend the `lupa` tests for the focus writer.
- **Live check** in an isolated WezTerm (AGENTS.md "Live checks"): a scratch `claude`
  session with scratch `--settings`, driven to a permission prompt and to a finished turn.
  It **will show real desktop notifications and play sounds**, so tell the user before
  running it.

## Docs and housekeeping

- README: a hooks section (install, trust dialog, notify config, the sound pack).
- PLAN.md: §8's table updated to this status model (Stop → waiting, not idle). §11 M4's
  "Daemon" item moved to M5, and §5 notes that hooks write SQLite directly until then.
- AGENTS.md: the Claude Code modules, the `claude/` installer, the hook contract and its
  measured latency, `WEZTERM_PANE` in hooks (once verified), and module v3.
- CHANGELOG `## Unreleased` → Added.
- Re-pin enola after the structural change. New coordinator hotspots of the known kind
  get judged by absolute size (AGENTS.md "Architecture checks").

## Left to the builder

- Exact SQL, the event-row cap, and log rotation.
- The bundled tones' names and timbres. Keep them short and soft.
- How the transcript view renders markdown (plain text is fine).
- Exact key for "load earlier" and the column widths.
- Whether `status_since` for the waiting group's age uses the hook's clock or the event
  time (they're the same machine; pick one).

## Calls made in design (not asked of the user)

1. A finished turn counts as **waiting** until seen; PLAN had it as idle. *(The user
   agreed to this split.)*
2. No daemon in M4; the hook writes SQLite and pings directly.
3. No ping when the session's pane is already focused.
4. The waiting filter becomes a `w` toggle, since the group is always visible.
5. Sounds are generated tones, not downloaded ones.
6. Detail panel on `i`; transcript on `t` inside it (the deck's own `t` stays Theme).
7. Synchronous hooks with `timeout: 5`, to keep events ordered.
8. The pane join uses `WEZTERM_PANE` from the hook's environment, falling back to the
   `sessions/<pid>.json` lookup.
9. A seen permission prompt shows as working, because no event marks the approval.
10. Only the deck marks sessions seen. The focus file keeps a 64-entry history, so visits
    between polls, or while the deck is closed, still count.
11. A `permission_prompt` notification only stands in for a missing `PermissionRequest`,
    so one prompt pings once.
12. Claude panes without a record show a dim `·`, and the header suggests
    `cardak hooks install` when hooks are missing.
