# Project background (#9) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every WezTerm window shows its active pane's project as faint ASCII art (the project's logo, else its name) in the bottom-right corner, generated once per project, replaceable by the user, switchable in the theme picker.

**Architecture:** cardak makes each configured project's ASCII art once (`~/.config/cardak/logos/<slug>.txt`, recorded in `~/.local/state/cardak/backgrounds.json`), renders it to a PNG in the scheme's foreground color (`~/.cache/cardak/backgrounds/<hash>.png`), and lists the PNG with its size in cells in `cardak_projects.lua`. `cardak.lua` (module 5) sets a two-layer `background` override in `sync`, next to the scheme. The theme picker gains a switch, previewed through `cardak_preview`.

**Tech Stack:** Python 3.13, Pillow, pyfiglet, resvg-py, Textual, WezTerm Lua (tested with lupa).

**Spec:** `docs/superpowers/specs/2026-11-01-9-project-background-design.md`

## Global Constraints

- Corner bottom-right, opacity default **0.06**, art drawn in the scheme's foreground; no scheme and no `[defaults] theme` → `#c0c0c0`.
- Generated **once** per configured project (keyed by `real_path`); the text file is never overwritten; no regeneration anywhere; a deleted file means no background.
- Logo art at most **40 columns × 16 rows**.
- Only configured projects (`projects.toml`) get art.
- New runtime deps: `pillow`, `pyfiglet`, `resvg-py` (all MIT; resvg-py 0.5.0 has a manylinux x86_64 wheel). If resvg can't load an SVG, the SVG candidate is skipped.
- `cardak hook` and `cardak statusline` import none of them: `tests/test_cli.py::test_hook_imports_only_what_it_needs` and `::test_statusline_imports_only_what_it_needs` stay green. `core/config.py` imports nothing new.
- Layering: `collectors/` import nothing from cardak; `core/` never imports `ui`/`wezterm`/…; `wezterm/generate.py` may import `core` (it already does).
- `MODULE_VERSION` 4 → **5**. `apply(config, { background = false })` opts out; a `config.background` or `config.window_background_image` present at `apply` means cardak sets no background.
- Every written file atomic, only when content changes, listed in AGENTS.md's table.
- Measured in the spike (`spikes/07_background.py`, WezTerm 20240203): `set_config_overrides{ background = { {source={Color=bg}, width='100%', height='100%'}, {source={File=png}, repeat_x='NoRepeat', repeat_y='NoRepeat', horizontal_align='Right', vertical_align='Bottom', width='<cols>cell', height='<rows>cell', opacity=0.06, horizontal_offset='-1cell', vertical_offset='-1cell'} } }` works; **both** `width` and `height` must be given in cells (one alone stretches the image); per-layer `opacity` works; an OSC 11 pane tint is not visible once layers are set.

## Review Focus

1. A project path that isn't a git repo, or no longer exists → name art, no exception (Task 4: `test_missing_project_dir_gets_name_art`).
2. A corrupt or unreadable logo file (truncated PNG, SVG resvg rejects) → name art (Task 4: `test_broken_logo_falls_back_to_the_name`).
3. A project name pyfiglet can't draw (`Čardak`, `日本`) → letters folded to ASCII, else the plain name (Task 3: `test_name_art_folds_accents`, `test_name_art_with_no_drawable_letters`).
4. A user-edited art file that is empty, whitespace only, unreadable, or has tabs → empty/unreadable means no background, tabs expand to spaces (Task 4: `test_empty_art_means_no_background`, `test_tabs_in_art_expand`).
5. Two projects whose names slug the same (`my app`, `my-app`) → two different files (Task 4: `test_same_slug_gets_distinct_files`).

---

### Task 1: Config — `[background]` and a per-project switch

**Files:**
- Modify: `pyproject.toml` (dependencies: `pillow>=12`, `pyfiglet>=1.0.4`, `resvg-py>=0.5.0`; run `uv lock`)
- Modify: `src/cardak/core/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces:
  - `Project.background: bool = True` (TOML `background = false` per `[[project]]`; non-bool → `ConfigError` "project #N: 'background' must be true or false").
  - `@dataclass(frozen=True) class BackgroundSettings: enabled: bool = True; opacity: float = 0.06`; `Config.background: BackgroundSettings`. `[background] opacity` must be a number in (0, 1], `enabled` a bool; else `ConfigError`.
  - `add_project(path, name, project_path, theme=None, background: bool = True)` writes `background = false` only when False.
  - `set_project_background(path: Path, project_path: Path, on: bool) -> None`: off writes `background = false`; on removes the key. Shares the find-the-table code with `set_project_theme` through `_edit_project(path, project_path, edit: Callable[[Table], None])`.

- [ ] **Step 1: Write failing tests**

```python
def test_background_defaults(tmp_path): cfg = load(write(tmp_path, PROJECT)); assert cfg.background == BackgroundSettings(True, 0.06); assert cfg.projects[0].background is True
def test_project_background_off(tmp_path): ...  # `background = false` → Project.background is False
def test_background_table(tmp_path): ...  # [background] enabled=false, opacity=0.1 → BackgroundSettings(False, 0.1)
@pytest.mark.parametrize("body", ["opacity = 0", "opacity = 1.5", "opacity = 'x'", "enabled = 'no'"])
def test_bad_background_table(tmp_path, body): ...  # ConfigError
def test_bad_project_background(tmp_path): ...  # background = "no" → ConfigError mentioning project #1
def test_set_project_background_off_then_on_keeps_comments(tmp_path): ...  # off adds `background = false`, on removes the line; a comment elsewhere survives
def test_add_project_with_background_off(tmp_path): ...  # appended block has `background = false`
```

- [ ] **Step 2:** `uv run pytest tests/test_config.py -q` → the new tests FAIL.
- [ ] **Step 3:** Implement as in Interfaces (read `[background]` in a `_background(path, data)` like `_web`).
- [ ] **Step 4:** `uv run pytest tests/test_config.py tests/test_cli.py -q` → PASS (the hook import guards included).
- [ ] **Step 5:** Commit `feat(core): a background switch per project and [background] (#9)`.

### Task 2: Collectors — tracked files, README images, the monospace font

**Files:**
- Modify: `src/cardak/collectors/git.py`; Create: `src/cardak/collectors/logos.py`, `src/cardak/collectors/fonts.py`
- Test: `tests/test_git.py`, `tests/test_logos.py`, `tests/test_fonts.py`

**Interfaces:**
- Produces:
  - `git.tracked_files(top: Path, runner: Runner = subprocess.run) -> tuple[str, ...] | None` — `git --no-optional-locks ls-files -z` via `_git`; None on failure.
  - `logos.readme_images(project: Path) -> tuple[str, ...]` — from the first of `README.md`, `README.rst`, `README` (case-insensitive) at the project root: `<img … src="…">` and `![…](…)` targets in document order, relative paths only (no scheme, no leading `/`), `#…`/`?…` stripped. Unreadable/missing → `()`. Reads at most 256 KiB.
  - `fonts.monospace_font(runner: Runner = subprocess.run) -> Path | None` — `fc-match -f %{file} monospace`, 3 s timeout; None when missing, failing, or the path doesn't exist.

- [ ] **Step 1: Failing tests**: `test_tracked_files_splits_nul` (fake runner returns `"a.svg\0b/c.png\0"` → `("a.svg", "b/c.png")`), `test_tracked_files_none_on_failure`; `test_readme_images_in_order` (README with `<img src="apps/x/logo.svg" width=4>` then `![w](site/a.png)` then `![b](https://img.shields.io/x)` → `("apps/x/logo.svg", "site/a.png")`), `test_readme_images_missing_readme`; `test_monospace_font_path`, `test_monospace_font_missing_fc_match` (runner raises `FileNotFoundError` → None).
- [ ] **Step 2:** Run them → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** `uv run pytest tests/test_git.py tests/test_logos.py tests/test_fonts.py -q` → PASS.
- [ ] **Step 5:** Commit `feat(collectors): tracked files, README images and the monospace font (#9)`.

### Task 3: ASCII art — the name and a logo

**Files:**
- Create: `src/cardak/core/ascii_art.py`
- Test: `tests/test_ascii_art.py`, fixture `tests/fixtures/logos/mark.svg` (a small hand-written SVG: a filled circle on a transparent background)

**Interfaces:**
- Produces:
  - `MAX_COLS = 40`, `MAX_ROWS = 16`, `RAMP = " .:-=+*#%@"`.
  - `name_art(name: str) -> str` — pyfiglet `standard`, `width=1000`; the name folded with NFKD to ASCII first (combining marks dropped, `Đ`/`đ` → `D`/`d`); trailing spaces and blank lines stripped. Nothing drawable left → the name itself.
  - `load_image(path: Path) -> "Image.Image"` — `.svg` through `resvg_py.svg_to_bytes(svg_path=str(path), width=320)` then Pillow; anything else Pillow; result RGBA. Raises `ArtError` (defined here) on any failure (import PIL/resvg_py inside the function).
  - `logo_art(image: "Image.Image") -> str` — algorithm below.

```text
logo_art:
  ink = alpha channel if the image has any alpha < 250, else |luminance − luminance of the top-left pixel|
  crop to ink's bbox (> 8); none → ArtError("blank image")
  cols = MAX_COLS; rows = round(cols * h / w / 2)   # a cell is about twice as tall as wide
  if rows > MAX_ROWS: rows = MAX_ROWS; cols = max(1, round(rows * 2 * w / h))
  resize ink to (cols, rows) with Image.Resampling.BOX; char = RAMP[v * (len(RAMP) − 1) // 255]
  strip trailing spaces per line and blank lines at both ends
```

- [ ] **Step 1: Failing tests**:

```python
def test_name_art_is_figlet(): art = name_art("cardak"); assert len(art.splitlines()) == 5 and "\\___" in art
def test_name_art_folds_accents(): assert name_art("Čardak") == name_art("Cardak")
def test_name_art_with_no_drawable_letters(): assert name_art("日本") == "日本"
def test_logo_art_fits_the_cap(): art = logo_art(load_image(FIXTURES / "logos/mark.svg")); lines = art.splitlines(); assert len(lines) <= 16 and max(map(len, lines)) <= 40 and "@" in art
def test_logo_art_of_an_opaque_png(tmp_path): ...  # white 200×100 PNG with a black square in the middle → dense chars in the middle rows, no "@" in the corners
def test_wide_logo_keeps_its_aspect(): ...  # 400×50 transparent PNG, opaque bar → 40 cols, ≤ 3 rows
def test_blank_image_raises(): ...  # fully transparent → ArtError
def test_load_image_rejects_garbage(tmp_path): ...  # b"not an image" as x.png and x.svg → ArtError
```

- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** `uv run pytest tests/test_ascii_art.py -q` → PASS.
- [ ] **Step 5:** Commit `feat(core): ASCII art from a project's name or logo (#9)`.

### Task 4: Backgrounds — find the logo, make the art once, render the image

**Files:**
- Create: `src/cardak/core/backgrounds.py`
- Test: `tests/test_backgrounds.py`

**Interfaces:**
- Consumes: Task 1 `Config`, `Project.background`, `BackgroundSettings`; Task 2 `tracked_files`, `readme_images`, `monospace_font`, `git.toplevel`; Task 3 `name_art`, `logo_art`, `load_image`, `ArtError`; `core.themes.Scheme`.
- Produces:
  - `@dataclass(frozen=True) class Image: file: Path; cols: int; rows: int` (PNG path and its size in cells).
  - `@dataclass(frozen=True) class Paths: art_dir: Path; record: Path; cache: Path` and `Paths.default(env=None)` → `config_dir()/"logos"`, `state_dir()/"backgrounds.json"`, `cache_dir()/"backgrounds"`.
  - `FALLBACK_COLOR = "#c0c0c0"`.
  - `pick_logo(readme: Sequence[str], tracked: Sequence[str]) -> str | None` — the spec's order: README images (existing tracked or not) first, then tracked image files (`svg png jpg jpeg webp ico`) whose base name contains `logo`, then `mark`/`wordmark`, then `icon`; within a rank a name with `dark` first, then the shortest path; skip `SKIP_NAMES = {"next.svg", "vercel.svg", "file.svg", "globe.svg", "window.svg", "favicon.ico"}` and any path with a part in `{"screens", "screenshots", "design", "node_modules", "test", "tests", "fixtures"}`.
  - `art_files(projects: Sequence[Project], art_dir: Path) -> dict[Path, Path]` — keyed by `project.path`; slug = lowercased name, runs of chars outside `[a-z0-9._-]` → `-`, stripped of `-.`, empty → `project`; a slug two projects share gets `-<first 6 hex of sha1(str(real_path))>` on both.
  - `read_art(file: Path) -> str | None` — text with tabs expanded, trailing whitespace stripped; None when missing, unreadable, or blank.
  - `ensure_art(config: Config, paths: Paths, find_logo: Callable[[Path], Path | None] = find_logo) -> list[str]` — for each first-per-path project with background on (and `config.background.enabled`) whose `real_path` isn't in the record: write `logo_art(load_image(logo))` or, on no logo or `ArtError`, `name_art(name)` with `open(…, "x")` (an existing file is kept), then add the path to the record (JSON `{"version": 1, "generated": [...]}`, atomic). A write failure is a problem string and isn't recorded. Returns problems.
  - `find_logo(project: Path) -> Path | None` — `toplevel`, `tracked_files`, `readme_images`, `pick_logo`; the result must exist under the project.
  - `render(text: str, color: str, cache: Path, font: Path | None) -> Image` — PNG of the text in `color` on transparent, 28 px; `font` None → `ImageFont.load_default(28)`; file `cache/<sha1(text, color, font)[:16]>.png`, written only if missing (atomic); cols = longest line, rows = line count.
  - `images(config: Config, schemes: Mapping[str, Scheme], paths: Paths, font: Path | None) -> tuple[dict[Path, Image], list[str]]` — `ensure_art`, then per project with background on: `read_art` → `render` in its scheme's foreground (else `[defaults] theme`'s, else `FALLBACK_COLOR`). Removes `*.png` in `cache` not used by this call. Global off → `({}, [])`.
  - `preview_image(project: Project | None, scheme: Scheme | None, paths: Paths, font: Path | None) -> Image | None` — the art of a configured project in that scheme's foreground; None for an unconfigured project or no art. No cache pruning.

- [ ] **Step 1: Failing tests** (fake `find_logo` everywhere except the `find_logo` tests):

```python
def test_pick_logo_prefers_the_readme(): assert pick_logo(["apps/b/podlog-logo-dark-theme.svg"], ["x/logo.png"]) == "apps/b/podlog-logo-dark-theme.svg"
def test_pick_logo_ranks_logo_mark_icon(): assert pick_logo([], ["a/icon.png", "a/cardak-mark-dark.svg", "public/next.svg"]) == "a/cardak-mark-dark.svg"
def test_pick_logo_prefers_dark_then_short(): assert pick_logo([], ["p/flatpare_logo_blue.svg", "p/flatpare_logo_dark.svg", "p/flatpare_logo.svg"]) == "p/flatpare_logo_dark.svg"
def test_pick_logo_skips_screens_and_boilerplate(): assert pick_logo([], ["design/screens/01-quiet/logo.png", "public/vercel.svg"]) is None
def test_art_made_once_and_never_again(tmp_path): ...  # ensure_art twice; delete the file; ensure_art → file still absent
def test_existing_art_file_is_kept(tmp_path): ...  # user's file present before first run → untouched, recorded
def test_broken_logo_falls_back_to_the_name(tmp_path): ...  # find_logo returns a garbage .png → file == name_art(name)
def test_missing_project_dir_gets_name_art(tmp_path): ...  # real find_logo on a path that doesn't exist
def test_project_with_background_off_gets_no_art(tmp_path): ...
def test_same_slug_gets_distinct_files(tmp_path): ...  # "my app", "my-app"
def test_empty_art_means_no_background(tmp_path): ...  # file with "  \n" → images() has no entry
def test_tabs_in_art_expand(tmp_path): assert read_art(f) == "a       b"  # "a\tb"
def test_render_is_cached_and_sized(tmp_path): ...  # same args → same file, mtime unchanged; cols/rows match the text
def test_images_use_the_scheme_foreground(tmp_path): ...  # PNG's opaque pixels are the scheme's fg; no scheme → #c0c0c0
def test_images_prune_unused_pngs(tmp_path): ...
def test_find_logo_in_a_git_repo(tmp_path): ...  # git init, add assets/logo.svg → that path
def test_write_failure_is_a_problem_and_not_recorded(tmp_path): ...  # art_dir is a file
```

- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** `uv run pytest tests/test_backgrounds.py -q` → PASS.
- [ ] **Step 5:** Commit `feat(core): make each project's art once and render it (#9)`.

### Task 5: The Lua module and its data — module 5

**Files:**
- Modify: `src/cardak/wezterm/generate.py`, `src/cardak/wezterm/cardak.lua`
- Test: `tests/test_generate.py`, `tests/test_lua.py`

**Interfaces:**
- Consumes: Task 4 `Image`; Task 1 `Config.background.opacity`.
- Produces:
  - `MODULE_VERSION = 5`.
  - `render_projects_lua(config, known, images: Mapping[Path, Image] | None = None)` adds `background_opacity = <opacity>,` at the top level and, per project with an image, `background = { file = <lua_str>, cols = N, rows = N }` in its entry (both paths of a symlinked project).
  - `write_generated(wezterm_dir, cardak_dir, config, schemes, images=None)` passes it through.
  - Lua: `previews[path]` becomes `{ scheme = …|nil, background = {file,cols,rows}|false|nil }`. Preview JSON gains optional `"background"`: an object, `false` (preview off), or absent (keep the saved one). A preview with `scheme == ""` clears the whole preview.
  - Lua `sync`: after the scheme, set `overrides.background` to the spike's two layers (base color = `wezterm.color.get_builtin_schemes()[scheme].background`, the table read once and cached; with no scheme cardak set, `window:effective_config().resolved_palette.background`) or `nil`. Track what cardak last set per `window:window_id()`; an `overrides.background` cardak didn't set is left alone; no write when nothing changed. Off when `apply` got `{ background = false }`, or `config.background`/`config.window_background_image` was non-nil at `apply`.

- [ ] **Step 1: Failing tests.** Extend the harness: `fake.color = { get_builtin_schemes = function() return SCHEMES end }`, `w:effective_config()` returning `{ resolved_palette = { background = '#111111' } }`, `w:window_id()` exists already.

```python
def test_lua_data_lists_backgrounds(): ...  # render_projects_lua with an Image → entry has background = { file = [[/c/x.png]], cols = 32, rows = 5 } and top-level background_opacity = 0.06
def test_module_version_5(): ...
def test_window_gets_its_project_background(): ...  # layers: [1].source.Color == scheme bg, [2].source.File == file, width == '32cell', height == '5cell', opacity == 0.06, horizontal_align == 'Right', vertical_align == 'Bottom'
def test_no_background_outside_a_project(): ...  # moving to a non-project cwd sets overrides.background = nil
def test_background_preview_on_and_off(): ...  # preview {"path","scheme","background":{...}} shows it; {"background": false} hides it; scheme "" restores saved
def test_a_background_set_by_someone_else_is_left_alone(): ...
def test_background_opt_out(): ...  # apply(config, { background = false }) → never set
def test_users_own_background_wins(): ...  # apply({ window_background_image = '/x.png' }) → never set
def test_no_redundant_background_writes(): ...  # sync twice → sets == 1
def test_module_4_data_still_loads_in_module_5(): ...  # data without background keys → schemes still applied, no background
```

- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement; bump the header line to `-- cardak-module: 5` and document the new `apply` option in the header comment. **Step 4:** `uv run pytest tests/test_lua.py tests/test_generate.py -q` → PASS.
- [ ] **Step 5:** Commit `feat(wezterm): the project's art as the window background (#9)`.

### Task 6: Wiring — the deck's writer and `cardak themes apply`

**Files:**
- Modify: `src/cardak/cli.py` (`_theme_writer`, `_themes_apply`, `_themes_zsh`, `_deck`)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: Task 4 `Paths`, `images`, `monospace_font`; Task 5 `write_generated(..., images=)`.
- Produces: `_theme_writer(wezterm_dir, cardak_dir, store, paths: Paths)` calls `backgrounds.images(config, store.get(), paths, font)` (font looked up once per writer, lazily) and passes the images; its problems join the warnings. `_themes_apply` does the same and prints them as warnings. All `cardak.core.backgrounds` imports stay inside these functions.

- [ ] **Step 1: Failing tests**: `test_themes_apply_makes_art_and_lists_the_image` (scratch config with one project in a tmp dir, module installed in a tmp wezterm dir → a `logos/*.txt` exists, `cardak_projects.lua` contains `background = {`), `test_themes_apply_twice_does_not_remake_deleted_art`, and a check that `python -X importtime -c "import cardak.cli"` output has no `PIL`, `pyfiglet`, `resvg_py` (add the three names to the existing hook/statusline import guards' forbidden lists).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** `uv run pytest tests/test_cli.py -q` → PASS.
- [ ] **Step 5:** Commit `feat(cli): write the backgrounds with the theme files (#9)`.

### Task 7: The picker's switch, its preview and saving

**Files:**
- Modify: `src/cardak/core/navigate.py` (`preview_request`, `Previewer.preview`), `src/cardak/ui/theme_picker.py`, `src/cardak/ui/app.py` (`action_theme`, `DeckSource` protocol), `src/cardak/core/source.py` (`set_theme` → `set_look`), `src/cardak/cli.py` (`_deck` passes the background tools)
- Test: `tests/test_navigate.py`, `tests/test_theme_picker.py`, `tests/test_app.py`, `tests/test_source.py`

**Interfaces:**
- Consumes: Task 1 `set_project_background`, `add_project(background=)`; Task 4 `preview_image`, `art_files`, `Paths`; Task 5 preview JSON.
- Produces:
  - `preview_request(project_path, scheme, nonce, background: dict | bool | None = None)`; `Previewer.preview(project_path, scheme, background: Image | bool | None = None) -> bool` (`Image` → `{"file","cols","rows"}`, `False` → `false`, None → key absent).
  - `@dataclass(frozen=True) class ThemeChoice: scheme: str; background: bool` in `ui/theme_picker.py`; `ThemePickerScreen(..., background: bool, art_file: Path | None, preview_image: Callable[[str], Image | None] | None)` dismisses with `ThemeChoice | None`.
  - The picker shows, under the list, a `Checkbox("Background: the project's ASCII art", value=background, id="background")` and a line `art: <art_file with ~>; replace its text to change it` (or `art is made when you save` when `art_file` is None). `ctrl+b` toggles it from the filter input. Toggling re-sends the preview of the highlighted scheme with the background (`preview_image(scheme)` when on, `False` when off).
  - `LiveSource.set_look(project_path: Path, theme: str, background: bool, name: str | None = None) -> None`: configured → `set_project_theme` if the theme changed, `set_project_background` if the switch changed; unconfigured → `add_project(..., theme=theme, background=background)`. Replaces `set_theme` (and its use in `DeckSource`). `LiveSource.project(path) -> Project | None`.
  - `DeckApp(..., backgrounds: BackgroundTools | None = None)` where `BackgroundTools` is a Protocol in `ui/theme_picker.py`: `art_file(project_path: Path) -> Path | None`, `preview_image(project_path: Path, scheme: str) -> Image | None`. `cli._deck` builds it from `Paths.default()`, `source.config`, the scheme store and `monospace_font()`.
  - In `action_theme`'s `done`: unchanged scheme and switch → clear preview and notify as today; else `source.set_look(...)`; the notification reads `"<name>: <scheme>"` plus `", background off"` when off.

- [ ] **Step 1: Failing tests**: `test_preview_request_carries_the_background` (three forms), `test_picker_returns_the_switch` (pilot: press ctrl+b then Enter → `ThemeChoice(scheme, False)`), `test_picker_previews_the_switch` (fake previewer records `(path, scheme, False)` after ctrl+b), `test_picker_shows_the_art_path`, `test_escape_discards_the_switch` (→ None, preview cleared), `test_set_look_writes_only_what_changed` (switch only → `background = false` written, theme line untouched), `test_set_look_adds_an_unconfigured_project_with_the_switch`, `test_deck_saves_background_off` (app test with fake source records `set_look(path, scheme, False, name)`).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** `uv run pytest -q` → PASS (whole suite: `set_theme`'s callers moved).
- [ ] **Step 5:** Commit `feat(ui): a background switch in the theme picker (#9)`.

### Task 8: Docs, checks and the live check

**Files:**
- Modify: `README.md` ("Jumping and themes": a "Project background" subsection), `CHANGELOG.md` (Unreleased → Added), `AGENTS.md` (layout lines for `core/{ascii_art, backgrounds}.py`, `collectors/{logos, fonts}.py`; the written-files table rows for `~/.config/cardak/logos/<slug>.txt` (once, never overwritten), `~/.local/state/cardak/backgrounds.json`, `~/.cache/cardak/backgrounds/<hash>.png`; the spike's measured facts under "Themes (M3)"; the stack's new deps)

- [ ] **Step 1:** Write the README subsection: what it shows, where the text file is, made once, replace it by pasting your own ASCII art (any size; the image follows it), turn it off per project in the picker (`t`, then ctrl+b) or for all with `[background] enabled = false`, `opacity`, `apply(config, { background = false })`, that a background set in `wezterm.lua` before `require('cardak').apply` wins, and that it hides the zsh tint. Re-run `cardak wezterm install` (module 5).
- [ ] **Step 2:** CHANGELOG: "Each project's window shows its name, or its logo, as faint ASCII art in the bottom-right corner; switch it per project in the theme picker (#9)".
- [ ] **Step 3:** `uv run ruff check && uv run ruff format --check && uv run pyright && uv run pytest --cov --cov-branch --cov-fail-under=80 -q` → all pass.
- [ ] **Step 4:** enola: `generate_snapshot` + `diff_snapshot` against the baseline; fix what improves the code, record accepted findings under "Architecture checks" in AGENTS.md.
- [ ] **Step 5:** Live check in a separate WezTerm (`--always-new-process`, scratch `XDG_CONFIG_HOME`/`XDG_STATE_HOME`/`XDG_CACHE_HOME`, own `gui-sock-<pid>`, capture by `_NET_WM_PID`, never focus): `cardak themes apply` on a scratch `projects.toml` listing the real podlog/cardak/flatpare/schnj paths (their trees are only read); screenshot a pane in each; confirm the art, corner, 6%, and the scheme color. Also time `ensure_art` for one project (expect well under 300 ms; if over, move generation to a worker and say so).
- [ ] **Step 6:** Commit `docs: the project background (#9)`.
