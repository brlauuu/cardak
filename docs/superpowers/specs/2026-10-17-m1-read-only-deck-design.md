# M1: read-only deck — design

Issue: #2. Milestone M1 in `PLAN.md` §11. Builds on the M0 findings recorded in
`PLAN.md` §4/§12 and `AGENTS.md`.

## Outcome

Running `cardak` (or `cardak tui`) in a WezTerm pane shows a live tree of every WezTerm
pane, grouped by project. Each pane row shows what runs in it, where it is, and how much
CPU/RAM its whole process tree uses. The tree refreshes every 2 seconds.

M1 is read-only, with one exception: "add as project" writes a new entry to the config
file. Jumping to a pane (M2), themes (M3), agent status (M4) and alerts (M5) are out of
scope.

**Success criteria**
- Opening or closing a pane, or `cd`-ing into another project, is reflected within ~2s.
- A pane running a CPU-heavy child (e.g. `cargo build` started from `claude`) shows
  that CPU on the pane's row and in its project's total.
- A `claude` pane shows its session name.
- CI (ruff, pyright, pytest with an 80% branch-coverage floor) is green.

## Grouping rules

`ProjectResolver.resolve(cwd)` returns, in priority order:
1. **Configured project**: the configured `path` that is a prefix of `cwd` (equal, or
   followed by `/`); the longest one wins.
2. **Unconfigured git project**: `git -C <cwd> rev-parse --show-toplevel` succeeds → a
   group keyed by that root, named after its basename, shown dimmed with the label
   "not in config".
3. **Other**: everything else (`~`, `/tmp`, a pane with no cwd).

Group order: configured projects in config order, then unconfigured git projects
alphabetically, then Other. Panes inside a group are ordered by `(window_id, tab_id, pane_id)`.
Groups with no panes are not shown.

## Components

```
src/cardak/
  config.py          Config/Project model, load, add_project
  resolver.py        ProjectResolver
  collectors/
    wezterm.py       the only WezTerm interface
    processes.py     per-tty process trees and resource totals
    claude.py        the only ~/.claude reader (M1: session meta by pid)
  snapshot.py        build_snapshot: pure function → DeckSnapshot
  ui/app.py          Textual app: refresh worker, tree, actions
  ui/widgets.py      row/label rendering
  cli.py             `cardak [tui] [--config PATH]`
```

Dependency direction: `cli → ui → snapshot → {collectors, resolver, config}`.
`collectors/*` import nothing else from `cardak`. `snapshot` does no I/O.

### config.py

- Path: `--config`, else `$CARDAK_CONFIG`, else `$XDG_CONFIG_HOME/cardak/projects.toml`
  (default `~/.config/cardak/projects.toml`).
- Schema as in `PLAN.md` §7: `[defaults]` (`theme`, `cpu_alert_pct`, `rss_alert_gb`,
  `notes_file`) and `[[project]]` (`name`, `path`, `theme`, `accent`, `github`). M1 uses
  `name`, `path` (with `~` expanded) and `accent`, and keeps the rest in the model for later milestones.
- A missing file is an empty config, not an error.
- An invalid file raises `ConfigError(path, line, message)`.
- `add_project(path_to_file, name, project_path)` **appends** a `[[project]]` block
  (serialized with `tomli-w`) to the existing text, so hand-written comments and layout
  survive. It writes to a temp file in the same directory and `os.replace`s it, creating
  the directory and file if missing. It refuses (`ConfigError`) when the file exists but
  does not parse, and when the path is already configured.

### resolver.py

- `ProjectResolver(config, git=run_git)`, where `git` is injectable for tests.
- The git lookup is cached per directory for the resolver's lifetime and runs with a
  1s timeout. A missing `git` binary, a timeout or a non-zero exit all mean "not a repo".
- Returns `Resolved(kind: "config" | "git" | "other", key: Path | None, name: str, accent: str | None)`.

### collectors/wezterm.py

- `find_socket(env, runtime_dir)`:
  1. `WEZTERM_UNIX_SOCKET` if it is set and exists.
  2. Otherwise the newest `$XDG_RUNTIME_DIR/wezterm/gui-sock-<pid>` whose pid is alive.
  3. Otherwise `sock` in that directory if it exists.
  4. Otherwise `None`.
- `list_panes(runner=subprocess.run)` runs
  `wezterm cli --no-auto-start list --format json` with `WEZTERM_UNIX_SOCKET` set to the
  discovered socket and a 3s timeout. It raises `WeztermUnavailable(reason)` when no
  socket is found, the binary is missing, the call exits non-zero, it times out, or the
  output is not JSON.
- `parse_panes(json_text) → list[Pane]`:
  `Pane(pane_id, window_id, tab_id, workspace, title, cwd: Path | None, tty: str | None, is_active)`.
  `cwd` is parsed from the `file://host/path` URL (host ignored, percent-decoded); an
  empty or missing cwd becomes `None`. Unknown fields are ignored.

### collectors/processes.py

- `ProcessSampler(table=PsutilTable())` keeps `psutil.Process` objects between polls
  (the CPU% needs two samples), keyed by `(pid, create_time)` so a reused pid is a new
  process.
- `sample(ttys) → dict[tty, PaneProcs]`:
  - **On the tty:** processes whose controlling terminal is the tty.
  - **Tree:** those plus all their descendants (a build started by `claude` has no tty but
    is a descendant).
  - **Foreground:** the process on the tty whose process group equals the tty's
    foreground process group (`tpgid`, field 8 of `/proc/<pid>/stat`), preferring the
    group leader. Fallback: the most recently started process on the tty.
  - **MCP split:** a *direct child* of a `claude` process counts as an MCP server when
    its command line starts with `npm exec`, `npx` or `uvx`, or contains `mcp`
    (case-insensitive). That child and all its descendants are summed separately as
    `mcp_cpu_pct` / `mcp_rss` and are not part of the pane's work total. Bash tool
    children (`sh`, `zsh`, `bash`) and their builds are work.
  - `PaneProcs(fg_name, fg_pid, cpu_pct: float | None, rss: int, n_procs, mcp_cpu_pct, mcp_rss)`;
    `cpu_pct` is `None` on a process's first sample.
- `NoSuchProcess` / `AccessDenied` / `ZombieProcess` are skipped per process.
- `PsutilTable` is the only code touching psutil and `/proc`; tests use a fake table.

### collectors/claude.py

- `session_meta(pid, sessions_dir=~/.claude/sessions) → ClaudeSession | None`:
  `ClaudeSession(session_id, name, cwd)`, read from `<pid>.json`. A missing file (a session
  with no prompt yet) or unparsable JSON returns `None`.

### snapshot.py

- `build_snapshot(panes, procs, resolver, claude_lookup, self_pane_id) → DeckSnapshot`.
- `DeckSnapshot(groups: tuple[Group, ...], n_panes, error: str | None, config_error: str | None)`.
- `Group(kind, key, name, accent, path, cpu_pct, rss, panes: tuple[PaneRow, ...])`.
- `PaneRow(pane_id, label, detail, cwd_display, cpu_pct, rss, is_self)`:
  - **`label`:** the foreground process name, or `claude · <session name>`, or `claude (new)`.
  - **`detail`:** the WezTerm pane title when it differs from the label.
  - **`cwd_display`:** relative to the group root (`.` at the root); `~`-abbreviated in Other.
- A group's totals are the sums of its panes' work totals; `None` CPU counts as 0.
- Frozen dataclasses, so they compare equal when nothing changed.

### ui/app.py, ui/widgets.py

- Textual `App` with a header line (`cardak · 3 projects · 7 panes`), a `Tree`, and a
  footer listing keys.
- A worker thread polls every 2s, builds the snapshot and posts it to the app. The app
  rebuilds the tree only when the snapshot changed.
- Expanded/collapsed state and the cursor are keyed by group key and `pane_id`, so they
  survive a rebuild. New groups start expanded.
- Group label: accent-colored name (configured), or dimmed `name · not in config` (git),
  or `Other`; then the `~`-abbreviated path, `CPU n%` and `RAM x.yG`.
- Pane row: `label`, `detail`, `cwd_display`, CPU and RAM, and `(this deck)` when `is_self`
  (`WEZTERM_PANE` is equal to the pane id).
- Keys: `a` add project, `r` refresh now, `q` quit. Arrow keys and the mouse navigate the tree.
- **Add as project (`a`):** enabled on an unconfigured git group or any of its panes
  (path = git root), and on a pane in Other (path = its cwd). It opens a modal with the
  name prefilled from the basename; Enter saves, Escape cancels. After saving, the config
  reloads and the next poll regroups.

## Errors

| Situation | Behaviour |
|---|---|
| WezTerm unreachable | The tree is replaced by `WezTerm not reachable: <reason> — retrying`; polling continues; a mux server is never spawned |
| Process exits mid-sample | Skipped; the pane stays, with totals from what was readable |
| `projects.toml` invalid | Red banner `projects.toml line N: <msg>`; grouping falls back to git/Other; `a` disabled until the file parses |
| `git` missing or slow | Treated as "not a repo" (cached, 1s timeout) |
| `~/.claude/sessions` unreadable | The pane shows `claude (new)` |

## Testing

- **Fixtures** in `tests/fixtures/`, sanitized: `/home/user/...` paths, made-up projects
  (`webshop`, `api-gw`). `wezterm_list_basic.json` (4 panes, 3 windows) and
  `wezterm_list_edge.json` (missing cwd, hostname in the URL, percent-encoded path).
- `test_wezterm.py`: parsing, socket discovery (temp runtime dir, live/dead pids),
  and `list_panes` error mapping via a fake runner.
- `test_processes.py`: a fake table with ttys, a process tree, tpgid, MCP children and
  vanishing processes.
- `test_resolver.py`: prefix matching (longest wins, `/repos/a` vs `/repos/ab`), a
  temp-dir `git init`, a fake git for timeouts and missing binaries, caching.
- `test_config.py`: load, defaults, a missing file, line numbers in errors, `add_project`
  preserving comments, creating the file, refusing duplicates and broken files.
- `test_claude.py`: present, missing and malformed session files.
- `test_snapshot.py`: grouping order, totals, labels, `is_self`.
- `test_app.py`: a Textual `run_test()` pilot with a stub source. Covers groups rendering,
  collapse surviving a refresh, the unreachable message, and `a` → modal → the config written.
- `-m live` (deselected by default): a smoke test against the real WezTerm GUI.

## Tooling (first commit)

- `uv init --package`, `requires-python = ">=3.12"`, version `0.1.0.dev0` in `pyproject.toml` only.
- Runtime deps: `textual`, `psutil`, `tomli-w`. Dev deps: `pytest`, `pytest-cov`,
  `pytest-asyncio`, `ruff`, `pyright`.
- Console script `cardak = "cardak.cli:main"`.
- `.github/workflows/test.yml`: `uv sync --locked`, `ruff check`,
  `ruff format --check`, `pyright`, `pytest --cov=cardak --cov-branch --cov-fail-under=80`.
- After merge: release `0.1.0` (CHANGELOG "Added" line, `release: cut 0.1.0`, tag
  `v0.1.0`), then suggest protecting `main` with the `test` check required.
- Re-pin the enola baseline once `src/cardak/` lands.
