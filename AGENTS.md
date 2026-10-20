# Repo conventions

cardak is a project-centric deck for WezTerm panes and Claude Code sessions: it groups
every pane by project, jumps to a pane on click, sets a color scheme per project, shows
agent status (working / waiting / idle), and attributes CPU/RAM to the pane that caused it.
`PLAN.md` is the spec; read it before starting on a milestone.

## Stack
- **Python 3.13**, managed with **uv** (`uv run`, `uv add`; never pip into the system).
- **Textual** for the TUI, served in a browser via `textual-serve`.
- **psutil** for processes, **SQLite** for storage, TOML config at `~/.config/cardak/projects.toml`:
  read with `tomllib`, new projects appended with `tomli-w`, existing keys edited in place with
  **`tomlkit`** (keeps comments and layout).
- **WezTerm** is the only supported terminal (`wezterm cli` plus a generated Lua module).
- Developed and tested on Kubuntu 24.04, KDE Plasma 5.27 on **X11**, WezTerm 20240203.

## Layout
```
pyproject.toml
src/cardak/
  cli.py                  # cardak [tui]; later: web|daemon|hook|hooks install|themes apply
  core/{config.py, resolver.py, snapshot.py, source.py}
  collectors/{wezterm.py, processes.py, claude.py}   # M4 adds hooks.py, transcripts.py
  store.py  alerts.py  daemon.py                      # later milestones
  ui/{app.py, widgets.py}                             # M4 adds detail.py
  wezterm/{generate.py, cardak_snippet.lua}           # M3
skills/{checkpoint,where-were-we}/SKILL.md
spikes/                   # M0 throwaway scripts (PEP 723 inline deps: `uv run spikes/x.py`)
tests/                    # mirrors src/cardak; fixtures/ holds sanitized recordings
```

## Code
- Small, single-purpose modules.
- **Layering:** `cli → {ui, wezterm} → core → collectors`. `core` never imports `ui` or
  `cardak.wezterm`, `collectors` import nothing from cardak, and `wezterm/` (the Lua
  module and its installer) imports nothing from cardak. `core/filter.py` is core's only
  Textual import (`textual.fuzzy`). `cli.py` is the only module at the
  package root besides `__init__.py`; anything else at that level makes `src/cardak`
  and its subpackages import each other (a directory-level cycle enola reports).
- **One interface per external format.** All WezTerm invocation and parsing lives in
  `collectors/wezterm.py`; all Claude Code parsing (session files, transcripts, hook
  payloads) lives behind one module. Nothing else touches raw `wezterm cli` output or
  files under `~/.claude/`. Neither is a stable API.
- Read-only by default: the deck observes and focuses panes; it does not launch or drive agents.
- Never edit `~/.config/wezterm/wezterm.lua`, `~/.claude/settings.json` or anything else
  under `~/.config` or `~/.claude` without asking. Generated files go to a path the user
  `require`s or opts into.

## Tests
- **pytest**, tests in `tests/`, mirroring `src/cardak/`.
- Parsers are tested against **recorded fixtures** in `tests/fixtures/`, never against the
  live machine. When a format surprises you, record it as a new fixture first, then fix.
  `spikes/out/wezterm_cli_list.json` is the first one.
- CI (from M1) runs `ruff check`, `ruff format --check`, `pyright` and
  `pytest --cov --cov-branch --cov-fail-under=80`. Don't lower the floor to get green;
  cover the code or explain the exclusion here.

## WezTerm (measured in M0, see PLAN §4)
- **Always call `wezterm cli --no-auto-start`.** Without it, a `wezterm cli` call with no
  GUI running silently spawns a headless `wezterm-mux-server`, which then shadows the
  real GUI for later calls.
- **Outside a WezTerm pane, `wezterm cli` does not find the GUI.** It only tries the mux
  socket `$XDG_RUNTIME_DIR/wezterm/sock`. Set `WEZTERM_UNIX_SOCKET` to the GUI's
  `gui-sock-<pid>` in the same directory, and re-resolve after a connection failure (the
  pid changes when the GUI restarts). Inside a pane the variable is already set.
- **Jumping** (`core/navigate.py`): `activate-pane` selects the pane but does not raise
  its window, so the deck then writes `OSC 1337 SetUserVar=cardak_focus=base64(<pane_id>:<nonce>)`
  to its **own** `/dev/tty`, and `cardak.lua` (shipped in `src/cardak/wezterm/`) raises the
  target window. It never writes to another pane's tty. KWin allows the raise because the
  request comes from the focused WezTerm window (verified live in M2). From anywhere else
  (the browser front-end, M7), use the EWMH `_NET_ACTIVE_WINDOW` request with source=2
  (`spikes/02_activate_pane.py --fallback`).
- `cardak wezterm install` installs `cardak.lua` (only over a file starting with
  `-- cardak: generated`, else `--force`); users add `require('cardak').apply(config)` to
  `wezterm.lua` themselves. cardak never reads or edits `wezterm.lua`.
- **Textual gotchas (M2):** `AUTO_FOCUS` focuses the first focusable widget in compose
  order even when it is hidden, so the deck focuses its tree explicitly on mount; and
  `Tree.clear()` leaves the cursor on no line, so a rebuild without a target puts it on row 0.
- The X11 window title is the **active pane's title**; `set-window-title` does not change it.
- `cwd` in `wezterm cli list` comes from OSC 7, so it follows the shell, not the foreground
  process. Fallback when it is missing: `/proc/<pid>/cwd`.

## Themes (M3)
- Files cardak writes (all atomic, through symlinks, mode kept; never over a file whose first
  line lacks cardak's marker, except a CLI command given `--force`; only when content changes):

  | Path | Written by | Content |
  |---|---|---|
  | `~/.config/wezterm/cardak.lua` | `cardak wezterm install` | static module: focus, theming, preview |
  | `~/.config/wezterm/cardak_projects.lua` | deck on every config change; `cardak themes apply` | project path → scheme (only when cardak.lua is installed there) |
  | `~/.config/cardak/tints` | same | `path<TAB>#rrggbb` for the zsh hook |
  | `~/.config/cardak/tint.zsh` | `cardak themes zsh` | the opt-in per-pane tint hook |
  | `~/.cache/cardak/schemes-<wezterm version>.json` | scheme probe | built-in schemes and their colors |
- `cardak.lua`'s second line is `-- cardak-module: N`. Bump `MODULE_VERSION`
  (`wezterm/generate.py`) whenever the Lua module gains a feature the Python side relies on;
  the deck and `cardak themes apply` tell users with an older module to re-run
  `cardak wezterm install`.
- The scheme list comes from a headless probe: `wezterm --config-file <probe> ls-fonts`
  evaluates a config that dumps `wezterm.color.get_builtin_schemes()`, without opening a window.
- Preview is keyed by **project path** (`cardak_preview` user var with JSON), lives only in
  WezTerm's Lua state, and is cleared by any config reload, which is also what saving does.
- Live checks with a user WezTerm already running: start the test GUI with
  `--always-new-process`, address it through its own `gui-sock-<pid>`, and capture windows
  filtered by `_NET_WM_PID`, so nothing touches the user's windows.

## Claude Code (measured in M0)
- `~/.claude/sessions/<pid>.json` maps a running `claude` pid to `sessionId`, `cwd`,
  `status` and `name`. It is written **after the first prompt**; before that there is only
  a `<pid>.<hash>.key` file.
- A `claude` process's child tree includes its MCP servers (`npm exec @playwright/...`).
  Don't attribute their CPU/RAM to the agent's work.

## Processes (measured in M1)
- **Never use psutil's `Process.terminal()`** in a long-running process: psutil memoizes
  its device → tty map on first use, so panes opened after the deck started have no tty.
  `PsutilTable` reads `tty_nr` from `/proc/<pid>/stat` and rebuilds the map every snapshot.

## Commits and PRs
- Conventional commits with a scope where it helps: `feat(collectors): ...`,
  `fix(ui): ...`, `docs: ...`, `ci: ...`, `chore: ...`, `release: cut X.Y.Z`. End the subject
  with the issue number when there is one: `(#12)`.
- Work on a branch and open a PR; `main` only moves through merged PRs. `main` is
  protected: the `test` check must pass on an up-to-date branch, no force pushes or
  deletion, and the rule binds admins.

## Versioning and releases
- **The version lives in `pyproject.toml` only.** Nothing else quotes it.
- **`CHANGELOG.md` is the release notes.** Every merged PR adds a line under
  `## Unreleased`, grouped Added / Changed / Fixed, ending with the issue or PR number.
  Write for the person using the deck, not the person reading the diff.
- Release: graduate Unreleased into `## X.Y.Z — YYYY-MM-DD`, bump `pyproject.toml`, open
  `release: cut X.Y.Z` as its own PR, and tag `vX.Y.Z` on `main` only after that PR is
  merged and CI is green. **Minor** for a new user-visible capability, **patch** for fixes,
  dependencies, docs and internal work.
- **Nothing is tagged before there is something to run.** M0 (spikes) shipped untagged;
  the first tag is `v0.1.0`, when the M1 read-only deck works. The version stays 0.x until
  the deck covers M0–M5.

## Architecture checks (enola)
- `.enola/` is gitignored, so the baseline is per-machine and a re-pin does not travel in a PR.
- Re-pin with `enola baseline pin` after a deliberate structural change. A new language
  appearing (the first `.py` / `.lua` files) also makes the old baseline incomparable.
- **M1 re-pinned the baseline** after the review fixes. At the pin: no cycles and no
  complexity findings (M1 moved the core modules into `core/` to break a
  `src/cardak` ↔ `src/cardak/ui` cycle, and split `summarize`, `build_snapshot`,
  `add_project` and `PsutilTable.snapshot`).
- **Four hotspot findings are accepted as design**, reported but not failed:
  `config.load`, `LiveSource._poll`, `LiveSource._reload_if_changed` and
  `DeckApp._rebuild` (fan-in × fan-out 12–25). M2 adds `DeckApp._render` (5 × 3), which
  applies the filter, sets the header and rebuilds: the same kind of coordinator. Each one coordinates the steps of a
  single operation; splitting it would only move the fan-out somewhere else.
- enola's complexity and hotspot detectors flag **relative** outliers: fixing the
  top one promotes the next. Judge a new one by its absolute size, not by its
  being listed.
