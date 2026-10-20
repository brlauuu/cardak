# M3: per-project themes — design

Issue: #8. Milestone M3 in `PLAN.md` §11. Implements theme option **C** decided in M0
(`PLAN.md` §4): a full color scheme per window (A), plus an opt-in per-pane background tint (B).
Builds on M2's Lua module and its `cardak wezterm install`.

## Outcome

Each project in `~/.config/cardak/projects.toml` has a WezTerm color scheme. A window takes
the scheme of the project its active pane is in, and switches within about a second of a
`cd`, a tab change or a focus change. In the deck, `t` on a project opens a picker over all
built-in schemes. Moving through the list previews each scheme live on that project's
windows; Enter saves it to projects.toml, and Esc reverts. Groups in the deck use the
project's accent color, taken from the scheme when none is configured. An opt-in zsh hook
also tints each pane's background by project, so split panes from different projects stay
distinguishable.

**Success criteria**
- With the module installed, `cd ~/repos/webshop` in any pane re-themes its window to
  webshop's scheme within about a second, and `cd ~` returns it to the default.
- In the picker, the project's windows change scheme as the cursor moves, after about
  150 ms of stillness. Enter persists the scheme (projects.toml shows `theme = "…"` with
  comments intact). Esc restores the previous scheme.
- `cardak themes apply` regenerates the WezTerm data file from the CLI.
- `cardak themes zsh` sets up the tint hook, and panes are tinted after `source ~/.zshrc`.
- CI (ruff, pyright, pytest ≥ 80% branch coverage) is green.

Out of scope: theme editing (only built-in schemes), per-tab themes, and themes for
non-WezTerm terminals.

## Files cardak writes

| Path | Owner | Written by | Content |
|---|---|---|---|
| `~/.config/wezterm/cardak.lua` | cardak (marker) | `cardak wezterm install` | static Lua: focus (M2), theming and preview handlers |
| `~/.config/wezterm/cardak_projects.lua` | cardak (marker) | deck on config change; `cardak themes apply` | generated data: `return { default = …, projects = { { path = …, scheme = … }, … } }` |
| `~/.config/cardak/tints` | cardak | same triggers as above | `<path>\t#rrggbb` per project |
| `~/.config/cardak/tint.zsh` | cardak (marker) | `cardak themes zsh` | the static zsh hook that reads `tints` |
| `~/.cache/cardak/schemes-<version>.json` | cardak | scheme probe | built-in schemes and their colors, for one WezTerm version |

Rules:
- **Data files only when the module is installed:** `cardak_projects.lua` is written only
  when a cardak-marked `cardak.lua` exists in the same directory.
- **Only on change:** generated files are written only when their content changes, so
  WezTerm doesn't reload for nothing.
- **Safe writes:** every write is atomic, writes through symlinks, keeps the mode of an
  existing file, and refuses to overwrite a file without cardak's marker unless `--force`
  is given (CLI only).

## Module version

`cardak.lua` gains a second header line: `-- cardak-module: 2`. M2's module (version 1) has no
such line.
- `cardak themes apply` and the deck's startup read the installed `cardak.lua`. If its
  module version is below 2, they report `Your cardak.lua is from an older cardak; run
  \`cardak wezterm install\` to enable themes` (once per run in the deck; exit code 0 in
  the CLI, since the data file is still written).
- `cardak wezterm install` replaces a version-1 file as usual, because it carries the marker.

## WezTerm side (`cardak.lua`)

- **Loading:** `local ok, data = pcall(require, 'cardak_projects')`, falling back to
  `{ projects = {} }`.
  - `require` caches modules, but WezTerm rebuilds the Lua state on every config reload, and
    it reloads when any `require`d file changes. So a regenerated data file applies on the
    next reload.
- **`update-status` handler (theming):**
  - finds the window's active pane and its cwd (Url object → `file_path`);
  - picks the project whose path equals the cwd or prefixes it at a `/` boundary, longest
    path first;
  - if a preview is active for that project path, its scheme wins; otherwise it uses the
    project's scheme, else `data.default`, else `nil` (no override, so the user's own
    `config.color_scheme` applies);
  - calls `window:set_config_overrides(overrides)` only when `overrides.color_scheme`
    differs, keeping any other override keys.
- **Preview handler:**
  - triggered by `user-var-changed` with name `cardak_preview`, whose value is JSON
    `{"path": "<abs project path>", "scheme": "<name or empty>", "n": <nonce>}`;
  - decoded with `wezterm.json_parse` inside `pcall`;
  - stores `previews[path] = scheme`, or clears it for an empty scheme. The next
    `update-status` applies it, within a second, and the handler also re-applies
    immediately for the window that sent the request;
  - previews live in Lua module state and vanish on config reload. Saving a scheme reloads
    the config, which clears the preview, and the saved scheme takes over.
- **Never errors:** handler bodies run inside `pcall`, as in M2.
- **Both** the M2 focus handler and these handlers are registered by `M.apply(config)`.

## Python side

### `core/config.py`

- Uses `tomlkit` for in-place edits: a new runtime dependency.
- `set_project_theme(path, project_path, theme)`:
  - loads the document with `tomlkit`;
  - finds the `[[project]]` entry whose expanded `path` equals `project_path`, and sets
    `theme`;
  - `ConfigError` if the project is not found, or if projects are written inline
    (`project = [...]`) — "edit it by hand";
  - all `add_project` safety rules apply: refuse an unparsable file, re-validate the result
    with `load`, atomic write through symlinks, keep the mode, `OSError` → `ConfigError`.
- `add_project(path, name, project_path, theme=None)` gains an optional `theme`, written as
  a key in the appended block.
- `Project` gains `tint: str | None`.
- Scheme names are free strings. Validating them against the scheme list happens in
  generation, not in load.

### `core/themes.py` (pure)

- `Scheme(name, background, foreground, cursor_bg, ansi: tuple[str, ...])`, with colors as
  `#rrggbb`.
- `default_accent(scheme) -> str`:
  - returns `cursor_bg` when its contrast against both `background` and `foreground` is at
    least 1.5 (WCAG relative-luminance ratio);
  - otherwise returns `ansi[5]` (magenta).
- `tint(background, accent, amount=0.10) -> str`: linear RGB mix, rounded, as `#rrggbb`.
- `project_accent(project, schemes) -> str | None`: the configured `accent`, else the
  scheme's default accent, else `None`.
- `project_tint(project, default_scheme, schemes) -> str | None`: the configured `tint`,
  else `tint(scheme.background, accent)`, else `None`.

### `wezterm/schemes.py`

- `load_schemes(cache_dir, runner) -> dict[str, Scheme]`:
  1. reads `wezterm --version` to key the cache;
  2. returns the cached JSON when present;
  3. otherwise writes a probe Lua file to a temp dir and runs
     `wezterm --config-file <probe> ls-fonts --list-system` with a 20 s timeout and
     `CARDAK_SCHEMES_OUT` set. The probe writes `{name: {background, foreground, cursor_bg,
     ansi}}` as JSON, which is validated, cached atomically and returned.
- `SchemesUnavailable(reason)` on a missing binary, timeout or invalid output.
- The probe never starts a GUI, and cleans up its temp dir.

### `wezterm/generate.py`

- `render_projects_lua(config, known: set[str] | None) -> tuple[str, list[str]]`:
  - returns the Lua text plus a list of problems (unknown scheme names when `known` is
    given; those projects are left out);
  - paths and names are emitted as Lua long-bracket strings.
- `render_tints(config, schemes) -> str`.
- `write_generated(wezterm_dir, cardak_dir, config, schemes) -> GenerateResult(written:
  list[Path], problems: list[str], module_version: int | None)` applies the rules in "Files
  cardak writes".
- `install_tint_hook(cardak_dir) -> Path` writes `tint.zsh`.

`tint.zsh` behaviour:
- defines a `chpwd` hook and also runs it once when the file is sourced (so a new shell is tinted at once);
- reads `~/.config/cardak/tints` (re-read when its mtime changes) and finds the longest
  matching path prefix;
- emits `OSC 11` with that color, or `OSC 111` (reset) outside projects;
- does nothing when not inside WezTerm (`$TERM_PROGRAM != WezTerm`).

### `core/source.py`

- `LiveSource(…, on_config: Callable[[Config], list[str]] | None = None)`. After every
  successful reload, `on_config(config)` runs. `cli` passes a closure that calls
  `write_generated` with the cached schemes, if any.
- Problems it returns join `config_error`'s banner as warnings: `config_warnings: tuple[str,
  …]` on `DeckSnapshot`, shown in the same banner in yellow.
- `LiveSource.set_theme(project_path, theme, name=None)`:
  - an existing project gets `set_project_theme`;
  - otherwise `add_project` with the theme;
  - in both cases followed by a reload under the existing lock.

### Snapshot and accents

`build_snapshot` gets an `accent_for: Callable[[Resolved], str | None]`:
- for configured projects it uses `project_accent`;
- git and other groups get none, as today.

### UI

- `t` on a config or git group (or a pane row, which resolves to its group) opens
  `ThemePickerScreen(project_name, project_path, current, schemes_loader, previewer)`.
- **Layout:**
  - an `Input`, then an `OptionList`;
  - each option is `██ ██ name`: background and foreground swatches as two colored blocks,
    then the name, with the current scheme marked `•`;
  - the filter is `textual.fuzzy.Matcher`, and the list keeps alphabetical order among
    matches.
- **Loading:** the scheme list loads in a thread worker, showing `loading schemes…` until
  it arrives. On `SchemesUnavailable` it shows the reason and accepts an exact typed name
  on Enter.
- **Preview:**
  - every highlighted-option change restarts a 150 ms timer, and when it fires
    `previewer.preview(project_path, scheme)` runs;
  - on Enter the screen dismisses with the scheme, and the app calls `source.set_theme`
    then `previewer.preview(project_path, "")`;
  - Esc calls `previewer.preview(project_path, "")` and dismisses with `None`.
- **Previewer** (`core/navigate.py`, beside `Navigator`): writes the `cardak_preview` user var
  to the deck's own `/dev/tty`, through the same write path and not-in-WezTerm rule as jumps.
- **After saving:** if the module isn't installed, or is version < 2, the deck notifies how
  to enable themes.
- **Deck startup:** when the scheme cache is missing, the probe starts in a background
  worker, so accents and the picker are ready without a wait.

### CLI

- `cardak themes apply`:
  1. loads the config and the schemes (probing if needed; a failure only disables unknown-
     name checks);
  2. runs `write_generated`, then prints what was written, the problems and the
     module-version warning.
  - Exit code 1 only on a config error.
- `cardak themes zsh`:
  - runs `install_tint_hook`, writes `tints`, and prints the line to add to `.zshrc`
    (`source ~/.config/cardak/tint.zsh`);
  - never edits `.zshrc`.

## Errors

| Situation | Behaviour |
|---|---|
| `cardak.lua` not installed | No `cardak_projects.lua`; the picker still saves; deck/CLI say how to enable themes |
| `cardak.lua` older than module 2 | Data file still written; the deck says to re-run `cardak wezterm install` (once per run) |
| Scheme probe fails | Picker shows the reason and accepts an exact name; generation skips name validation |
| Unknown scheme in projects.toml | That project is left out of `cardak_projects.lua`; the banner warns `projects.toml: unknown scheme "…" for <name>` |
| projects.toml unparsable or inline projects | Saving refused with the reason; `t` disabled like `a` while the file is broken |
| A file to write has no cardak marker | Not overwritten; warning names the file and `--force` (CLI) |
| Deck not inside WezTerm | Picker opens and saves; there is no preview, and a note says so |

## Testing

- `test_config.py`, `set_project_theme`:
  - comments and layout are kept, and only the `theme` key changes;
  - a `theme` is inserted when absent;
  - a project not found;
  - inline projects refused;
  - a broken file refused;
  - symlink and mode;
  - `add_project` with a theme.
- `test_themes.py`: contrast picks `cursor_bg` versus magenta, exact tint values,
  configured overrides.
- `test_schemes.py`: cache hit with no run, probe parse and validation, timeout, missing
  binary, bad JSON, cache keyed by version (fake runner; the probe Lua text is checked for
  `get_builtin_schemes` and `CARDAK_SCHEMES_OUT`).
- `test_generate.py`:
  - exact Lua and tints text, including long-bracket quoting;
  - unknown schemes skipped and reported;
  - no write when the module is absent;
  - no write when the content is unchanged;
  - marker refusal;
  - module-version detection.
- `test_source.py`: `on_config` runs after reloads, not on unchanged polls; warnings reach
  the snapshot; `set_theme` for existing and new projects.
- `test_app.py` / `test_theme_picker.py` (pilot, with stub loader and previewer):
  - `t` on a group opens the picker;
  - the filter works;
  - preview fires once after the debounce, not on every keystroke;
  - Enter saves and clears the preview, and Esc clears it;
  - a git group adds the project;
  - the probe failure path;
  - `t` is disabled on a broken config.
- `test_cli.py`: `themes apply` output and exit codes, `themes zsh` output.
- **Live check** (executor, scratch `XDG_CONFIG_HOME`, own WezTerm instance):
  - `cd` between two projects re-themes the window (verified via window capture without
    changing focus, as in M0);
  - picker preview and Esc revert;
  - Enter persists;
  - the tint hook tints a pane.

## Docs and release

- AGENTS.md: the generated files and their ownership, the module version, `tomlkit` as
  the in-place editor, and that preview is keyed by project path.
- PLAN.md §11 M3 boxes.
- CHANGELOG "Added" entries.
- Release `0.3.0`.
