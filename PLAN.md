# cardak — project-centric terminal & agent deck

> Developed and tested on Kubuntu 24.04 (KDE Plasma 5.27, X11) with WezTerm, zsh and Claude Code.

## 1. Problem

Working on several projects in parallel, each with one or more terminals and Claude Code sessions, means hopping between WezTerm tabs and GitHub issues and losing track of which session is where, what an agent is waiting on, and where work stopped. When a heavy build started by an agent takes the terminal down, nothing tells you which session triggered it.

## 2. Goals

1. **Project-first overview.** Group every running terminal pane by the project directory it lives in, not by window or tab.
2. **Click to jump.** Clicking a pane in the deck focuses that WezTerm pane, including raising its window.
3. **Per-project terminal themes.** One config file maps project paths to WezTerm color schemes (and a project accent color used by the deck).
4. **Agent awareness.** For each Claude Code session: working / waiting on you / idle, plus a one-line "what it needs from you".
5. **Expandable detail.** Open a session to see recent messages, tool calls and whatever reasoning is recorded in its transcript.
6. **Resource attribution.** CPU / RAM per pane, *including child processes* (builds, test runners), with an event log when something spikes.
7. **Checkpoint & recap.** Per project: "save where we stopped" into a project note, and "walk me through where we stopped".
8. **Two front-ends, one core.** TUI with mouse support first, browser view second.

## 3. Non-goals (for now)

- Launching or orchestrating agents (no worktree management, no kanban, no auto-replies).
- Supporting terminals other than WezTerm.
- Remote machines.
- Cost dashboards (other tools already do this well).

## 4. Key decisions

| Decision | Choice | Why | Revisit if |
|---|---|---|---|
| Terminal substrate | **WezTerm only** (tested on 20240203) | `wezterm cli list --format json` gives `pane_id`, `window_id`, `tab_id`, `workspace`, `title`, `cwd`, `tty_name` (M0: populated for every pane); Lua config can set color schemes per window. Note `activate-pane` only selects the pane inside its window, it does **not** raise the window (M0) | Need to support other terminals |
| Invoking `wezterm cli` | Always pass `--no-auto-start`; when not inside a pane, set `WEZTERM_UNIX_SOCKET` to `$XDG_RUNTIME_DIR/wezterm/gui-sock-<pid>` | Without a GUI, `wezterm cli` silently spawns a headless `wezterm-mux-server`; outside a pane it only looks at the mux `sock`, not the GUI socket (M0) | WezTerm changes socket discovery |
| Jump to pane (raise + focus) | **Primary:** write OSC 1337 `SetUserVar=cardak_focus=<nonce>` to the pane's `tty_name`; a `user-var-changed` Lua handler calls `pane:activate()` + `window:focus()`. **Fallback (X11):** EWMH `_NET_ACTIVE_WINDOW` with source=2 (pager) via `python-xlib` | M0 on KDE 5.27 / X11: user-var works when WezTerm already has focus (the TUI case), and KWin blocks it when another app has focus; the EWMH pager request raised the window every time, from any app. No X window ids needed for the primary path, and no system packages | Wayland, or the browser front-end (M7), where focus is outside WezTerm → EWMH (X11) or KWin scripting over D-Bus |
| Language / UI | **Python + Textual** | Mouse-clickable TUI, and `textual-serve` can expose the same app in a browser, giving one codebase for both front-ends. | Startup time or perf becomes a problem → Go + Bubble Tea |
| Process data | `psutil` | cwd, tty, cpu, rss, children per pid | — |
| Agent state | **Claude Code hooks** (primary) + `~/.claude/sessions/<pid>.json` (pid → `sessionId`, `cwd`, `status`, `name`) + transcript JSONL in `~/.claude/projects/` (detail) | Hooks are event-driven and reliable; the per-pid session file makes the pane → session join exact (M0); transcripts hold the full history | Session file format changes |
| Storage | SQLite (`~/.local/share/cardak/cardak.db`) | Event log, resource history, hook events | — |
| Config | TOML at `~/.config/cardak/projects.toml` | Human-editable, single source of truth | — |
| Theming mechanism | cardak **generates** `~/.config/wezterm/cardak_projects.lua`; `wezterm.lua` `require`s it and calls `.apply(config)`. The same module hosts the focus handler | Avoids parsing TOML in Lua; WezTerm auto-reloads config, including `require`d modules (M0) | — |
| Theme granularity | **C: A + optional B.** A: `update-status` handler sets the window's scheme from the active pane's cwd. B: opt-in zsh `chpwd` hook emits an OSC 11 background tint per pane | See below | Tints get in the way of TUIs |

### Theme granularity (decided in M0: C)

WezTerm's `window:set_config_overrides({ color_scheme = ... })` is **per window**, not per pane. Options:

- **A. One window (or workspace) per project.** A Lua handler sets the window's scheme from the active pane's cwd. Simple and robust. Recommended.
- **B. Per-pane tint via OSC escape sequences.** A zsh `chpwd` hook emits OSC 10/11 (fg/bg) based on the project. Works with mixed panes in one window, but only changes fg/bg, not the full palette, and child programs can override it.
- **C. Both:** A for the full scheme, B as a subtle background tint fallback.

M0 findings (`spikes/04_themes`, `spikes/05_osc_tint.zsh`): A works and re-themes within ~1s of a `cd`. But with two projects split in one tab, the **whole window palette flips** whenever focus moves between the panes. For anyone who keeps several projects as tabs/splits in one window, A alone would look jumpy in that case. B's OSC 11 tint stays per pane and survives A's palette flips, which keeps the panes of a mixed window distinguishable. So: A is the default (full palette, no shell setup, follows the active tab), and B is an opt-in zsh snippet that cardak ships for mixed windows.

## 5. Architecture

```
            ┌───────────────────────────── cardak daemon (asyncio) ───────────────────────────────┐
 wezterm ──▶│ PaneCollector     every 1–2s: `wezterm cli list --format json`                       │
            │ ProcessCollector  every 2s: psutil; pane tty → foreground pid → process tree cpu/mem │
 Claude  ──▶│ HookReceiver      unix socket / localhost HTTP; receives hook JSON                   │
 Code       │ TranscriptReader  tails ~/.claude/projects/<enc-cwd>/<session>.jsonl on demand      │
            │ ProjectResolver   cwd → project (config paths first, then git toplevel)             │
            │ AlertEngine       thresholds → events table, desktop notification (notify-send)     │
            │ Store (SQLite)                                                                      │
            └──────────────┬──────────────────────────────────────────────────────────────────────┘
                           │ local API (unix socket, JSON)
             ┌─────────────┴─────────────┐
             │  Textual app (TUI)        │──▶ `wezterm cli activate-pane`, `send-text`
             │  same app via textual-serve (browser)
             └───────────────────────────┘
```

Start simple: in M1 the collectors can run *inside* the Textual app. Split into a daemon only when the hook receiver needs to be always-on (M4).

### Joining the data

1. `wezterm cli list` → panes with `tty_name` and `cwd` (cwd comes from OSC 7, so make sure the shell emits it; WezTerm's shell integration does).
2. For each pane, find processes whose controlling tty == `tty_name` → foreground process group → full child tree.
3. If the tree contains a `claude` process: read `~/.claude/sessions/<pid>.json` for `sessionId`, `cwd`, `status` (verified in M0). The file is only written after the first prompt, so a brand-new session shows as "claude (new)" until then, or until its `SessionStart` hook arrives.
4. Hook events carry `session_id` and `cwd`, so they join to the pane through step 3.
5. `cwd` → project via `ProjectResolver`.

## 6. Data model (SQLite)

- `projects(id, name, root_path, theme, accent, github_repo, notes_path)`, mirrored from config
- `panes(pane_id, window_id, tab_id, workspace, tty, cwd, project_id, title, first_seen, last_seen)`
- `agent_sessions(session_id, pane_id, project_id, tool, status, status_since, waiting_reason, last_assistant_snippet, transcript_path)`
- `samples(ts, pane_id, cpu_pct, rss_bytes, n_procs, top_child_cmd)`, ring-buffered (e.g. keep 24h)
- `events(ts, kind, pane_id, project_id, session_id, payload_json)`; kinds: `hook.*`, `resource.spike`, `pane.closed`, `checkpoint.saved`

## 7. Config file

```toml
# ~/.config/cardak/projects.toml
[defaults]
theme = "Tokyo Night"
cpu_alert_pct = 300        # summed across the pane's process tree (cores × 100)
rss_alert_gb  = 8
notes_file = ".cardak/handoff.md"   # relative to project root

[[project]]
name  = "webshop"
path  = "~/repos/webshop"
theme = "Catppuccin Mocha"
accent = "#cba6f7"
github = "example/webshop"

[[project]]
name  = "api-gw"
path  = "~/repos/api-gw"
theme = "Gruvbox Dark (Gogh)"
accent = "#fabd2f"
```

`cardak themes apply` regenerates `cardak_projects.lua` from this file.

## 8. Claude Code integration

Hooks to register in `~/.claude/settings.json` (each runs `cardak hook <event>` which forwards stdin JSON to the daemon):

| Hook | Use |
|---|---|
| `SessionStart` | create/refresh `agent_sessions` row |
| `UserPromptSubmit` | status → working |
| `PreToolUse` / `PostToolUse` | current tool, activity timestamp |
| `Notification` | status → **waiting**; store the message as `waiting_reason` |
| `Stop` | status → idle; capture last assistant message snippet |
| `SessionEnd` | close session |

"What it's waiting on" = `waiting_reason` from the Notification hook + the last assistant message. Optionally summarise it to one line with a cheap model call later; v1 shows it raw.

Detail view reads the transcript JSONL lazily: messages, tool calls, and any recorded thinking blocks (collapsed by default).

## 9. Checkpoint & recap

Implemented as **Claude Code skills** first, so they work without the deck:

- `/checkpoint` appends a dated entry to `<project>/.cardak/handoff.md` covering the goal, what got done, decisions made, what's blocked on the user, the next concrete step, and branch / issue / PR links.
- `/where-were-we` reads `handoff.md`, plus `git log` and `git status` since the last entry, plus open issues (via `gh`), and gives a short briefing.

The deck adds two buttons per project: **Checkpoint** sends `/checkpoint` into the selected agent pane via `wezterm cli send-text`. **Recap** shows the latest handoff entry inline, or spawns a fresh Claude pane running `/where-were-we`.

## 10. UI sketch (TUI)

```
┌ cardak ─────────────────────────────────── 3 projects · 7 panes · 1 waiting ┐
│ ▼ webshop   ~/repos/webshop                        CPU 12%  RAM 1.1G        │
│    ● claude   working   "refactoring listing parser"        3m             │
│    ◐ claude   WAITING   "approve: run npm install?"          8m   ← click  │
│    ○ zsh      idle                                                          │
│ ▼ api-gw    ~/repos/api-gw                         CPU 340% ⚠ RAM 6.2G      │
│    ● claude   working   spawned: cargo build (290% CPU)                     │
│    ○ zsh      idle                                                          │
│ ▶ notes (collapsed)                                                         │
├─────────────────────────────────────────────────────────────────────────────┤
│ [c] checkpoint  [r] recap  [enter] detail  [click] jump  [t] themes  [/] find│
└─────────────────────────────────────────────────────────────────────────────┘
```

Design exploration (browser and TUI variants) can be done separately with Claude Design; see the brief in §13.

## 11. Milestones

**M0: Spikes (de-risk, ~1 evening).** Throwaway scripts, no app yet.
- [x] `wezterm cli list --format json` on the dev machine: confirm `cwd` and `tty_name` are populated for all panes (OSC 7 working in zsh + p10k).
- [x] `wezterm cli activate-pane --pane-id N` from *another* window: does KDE Plasma actually raise and focus the window, on both Wayland and X11? If not, test `kdotool` / `wmctrl` / a WezTerm Lua `user-var` trick.
- [x] Map a running `claude` pid → pane via tty.
- [x] Per-window color scheme from a cwd → scheme table in Lua (option A); try OSC 11 tint (option B).
- [x] Decide theme option A/B/C.

**M1: Read-only deck.**
- [ ] Tooling: `uv` package with the version in `pyproject.toml`; ruff, pyright, pytest + coverage floor; GitHub Actions CI (see AGENTS.md).
- [ ] Config loader + ProjectResolver.
- [ ] Pane + process collectors.
- [ ] Textual tree grouped by project, auto-refresh.
- [ ] Unassigned panes in an "Other" group, with an "add as project" action that writes to the TOML.

**M2: Navigation.**
- [ ] Click / Enter on pane → activate-pane (+ window raise from M0).
- [ ] Keyboard nav, fuzzy find.

**M3: Themes.**
- [ ] `cardak themes apply` generates the Lua file; `wezterm.lua` snippet documented in README.
- [ ] Theme picker in the TUI (lists built-in WezTerm schemes, live preview on the project's window).

**M4: Agent awareness.**
- [ ] Hook installer (`cardak hooks install`, idempotent, backs up settings.json).
- [ ] Daemon with HookReceiver + SQLite.
- [ ] Status dots, waiting reason, "waiting" filter, desktop notification on → waiting.
- [ ] Detail pane: transcript viewer with collapsible tool calls / thinking.

**M5: Resources.**
- [ ] Per-pane process-tree sampling, sparkline.
- [ ] Spike alerts with attribution ("pane 12 · api-gw · `cargo build` 290% CPU, 5.8 GB").
- [ ] Event log view; survives the terminal crashing because the daemon runs outside WezTerm (systemd user service).

**M6: Checkpoint & recap.**
- [ ] Two skills (usable standalone).
- [ ] Buttons in the deck.

**M7: Browser.**
- [ ] `cardak web` via textual-serve, bound to 127.0.0.1.

## 12. Risks

- **Window focus.** Resolved for X11 in M0 (see Key decisions). Still open: Wayland (KWin scripting over D-Bus is the likely route), and the browser front-end, which needs the EWMH fallback because focus sits in the browser.
- **Socket discovery outside WezTerm.** A systemd daemon has no `WEZTERM_UNIX_SOCKET`, and after a GUI restart the `gui-sock-<pid>` name changes. The daemon must re-resolve it on every connection failure, and never let `wezterm cli` auto-spawn a mux server.
- **cwd accuracy.** It depends on OSC 7. Fallback is `/proc/<pid>/cwd` of the foreground process.
- **Claude Code internals** (session files, transcript format) are not a stable API. Keep parsing isolated in one module, and prefer hooks. M0: `sessions/<pid>.json` appears only after the first prompt, and a session banner said "Transcript saving is off", so the detail view must handle a missing transcript.
- **Attribution noise from MCP servers.** A `claude` process's child tree includes its MCP servers (`npm exec @playwright/...` etc.), not just the builds it launched. M5 must label or exclude them, e.g. children that live as long as the session vs. short-lived Bash tool children.
- **OSC 11 tint (option B)** is overridable by programs that set their own background, and it only colors the background, not the palette.
- **The terminal crash case.** If WezTerm itself dies, the TUI dies with it. The daemon (systemd user service) keeps the event log, so the post-mortem is still there.

## 13. Claude Design brief

> Design an observation deck for terminal sessions grouped by software project. Two variants: (1) a terminal UI rendered in a monospace grid, (2) a browser dashboard. Each project is a collapsible group with its accent color, root path, aggregate CPU/RAM, and a list of panes. Each pane row shows its type (agent / shell), status (working, waiting on me, idle, error), a one-line summary of what it's doing or what it needs, the elapsed time, and a resource warning if it's spiking. Waiting rows must stand out. Selecting a row opens a detail panel with recent agent messages, tool calls and collapsed reasoning. Per-project actions: Checkpoint, Recap, Theme. Dark theme, calm and dense, made for glancing.

## 14. Repo layout (proposed)

```
cardak/
  pyproject.toml          # uv-managed; deps: textual, textual-serve, psutil, tomli-w
  src/cardak/
    config.py  resolver.py
    collectors/{wezterm.py, processes.py, hooks.py, transcripts.py}
    store.py  alerts.py  daemon.py
    ui/{app.py, widgets.py, detail.py}
    wezterm/{generate.py, cardak_snippet.lua}
    cli.py                # cardak [tui|web|daemon|hook|hooks install|themes apply]
  skills/{checkpoint,where-were-we}/SKILL.md
  spikes/
  tests/
  CLAUDE.md  PLAN.md
```
