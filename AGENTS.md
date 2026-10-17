# Repo conventions

cardak is a project-centric deck for WezTerm panes and Claude Code sessions: it groups
every pane by project, jumps to a pane on click, sets a color scheme per project, shows
agent status (working / waiting / idle), and attributes CPU/RAM to the pane that caused it.
`PLAN.md` is the spec; read it before starting on a milestone.

## Stack
- **Python 3.13**, managed with **uv** (`uv run`, `uv add`; never pip into the system).
- **Textual** for the TUI, served in a browser via `textual-serve`.
- **psutil** for processes, **SQLite** for storage, TOML config at `~/.config/cardak/projects.toml`.
- **WezTerm** is the only supported terminal (`wezterm cli` plus a generated Lua module).
- Developed and tested on Kubuntu 24.04, KDE Plasma 5.27 on **X11**, WezTerm 20240203.

## Layout
```
pyproject.toml
src/cardak/
  config.py  resolver.py
  collectors/{wezterm.py, processes.py, hooks.py, transcripts.py}
  store.py  alerts.py  daemon.py
  ui/{app.py, widgets.py, detail.py}
  wezterm/{generate.py, cardak_snippet.lua}
  cli.py                  # cardak [tui|web|daemon|hook|hooks install|themes apply]
skills/{checkpoint,where-were-we}/SKILL.md
spikes/                   # M0 throwaway scripts (PEP 723 inline deps: `uv run spikes/x.py`)
tests/
  fixtures/               # recorded `wezterm cli list` output, hook payloads, transcript JSONL
```

## Code
- Small, single-purpose modules.
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
- **`activate-pane` does not raise the window**, only selects the pane inside it. Jump to a
  pane by writing OSC 1337 `SetUserVar=cardak_focus=<nonce>` to the pane's `tty_name`;
  the Lua handler calls `pane:activate()` + `window:focus()`. KWin honours that only when
  WezTerm already has focus. From anywhere else (the browser front-end), use the EWMH
  `_NET_ACTIVE_WINDOW` request with source=2 (`spikes/02_activate_pane.py --fallback`).
- The X11 window title is the **active pane's title**; `set-window-title` does not change it.
- `cwd` in `wezterm cli list` comes from OSC 7, so it follows the shell, not the foreground
  process. Fallback when it is missing: `/proc/<pid>/cwd`.

## Claude Code (measured in M0)
- `~/.claude/sessions/<pid>.json` maps a running `claude` pid to `sessionId`, `cwd`,
  `status` and `name`. It is written **after the first prompt**; before that there is only
  a `<pid>.<hash>.key` file.
- A `claude` process's child tree includes its MCP servers (`npm exec @playwright/...`).
  Don't attribute their CPU/RAM to the agent's work.

## Commits and PRs
- Conventional commits with a scope where it helps: `feat(collectors): ...`,
  `fix(ui): ...`, `docs: ...`, `ci: ...`, `chore: ...`, `release: cut X.Y.Z`. End the subject
  with the issue number when there is one: `(#12)`.
- Work on a branch and open a PR; `main` only moves through merged PRs.

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
- Last pinned: after M0, covering only `spikes/`. Re-pin when `src/cardak/` first lands in M1.
