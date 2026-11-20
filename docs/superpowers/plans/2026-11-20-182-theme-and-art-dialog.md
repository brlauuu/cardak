# Theme and Art Dialog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the theme picker with a two-pane "Theme and art" dialog: schemes left, art (switch, 3×3 position grid, links to its text files) right, Enter saving both from anywhere, a "none" row, and files opened with `xdg-open`.

**Architecture:** The dialog stays `ui/theme_picker.ThemePickerScreen` and keeps getting everything through protocols and callables. The Art pane is a new widget in `ui/art_pane.py` that owns the switch, grid and file lines and reports changes as messages. "No scheme of its own" is carried as `None` from the dialog through `ThemeChoice`, `LiveSource.set_look`, `config.set_project_theme` and the preview request (JSON `false`, Lua module 10). `xdg-open` lives in a new `collectors/desktop.py`, wired by `cli.py`.

**Tech Stack:** Python 3.13, Textual, tomlkit, Lua (WezTerm module), pytest.

**Spec:** `docs/superpowers/specs/2026-11-20-182-theme-and-art-dialog-design.md`

## Global Constraints

- Nothing is written to disk before Enter; Esc clears the preview and saves nothing.
- No typing into a shell, no new WezTerm tab.
- Layering per `AGENTS.md`: `collectors` import nothing from cardak; `ui` imports no `cardak.wezterm` / `cardak.llm`; `tests/test_layers.py` stays green.
- Names and paths shown in the dialog go through `textual.markup.escape`.
- `ctrl+b` and `ctrl+l` are removed.
- Every command runs as `cd /home/brlauuu/repos/cardak/.claude/worktrees/w182 && …`.
- Checks before each commit: `uv run ruff check && uv run ruff format --check && uv run pyright && uv run pytest -q`. Coverage floor 80 %.
- Commits: conventional, subject ending `(#182)`.
- enola: `set_baseline` before Task 1; `generate_snapshot` + `diff_snapshot` in Task 9.

## Review Focus

1. Enter while the schemes are still loading (or failed to load), after moving the art: the project's theme is kept, not removed. → Task 7.
2. A filter is typed, Tab to Art, Enter: the highlighted filtered row is saved, never "none". → Task 7.
3. A project name or path with `[` in it: shown literally in title, rows and file lines. → Tasks 6, 7.
4. A 60-column screen: the dialog stacks and nothing raises on mount or resize. → Task 7.
5. The art file was deleted between opening the dialog and pressing `e`: an error notice with the path, no crash. → Tasks 5, 8.

---

### Task 1: config — remove a theme, step a spot

**Files:** Modify `src/cardak/core/config.py`; Test `tests/test_config.py`

**Interfaces — Produces:**
- `set_project_theme(path: Path, project_path: Path, theme: str | None) -> None` — `None` deletes the `theme` key (no key: no write difference, still valid).
- `step_spot(at: Position, columns: int, rows: int) -> Position` — one step in the 3×3 grid of `COLUMNS` × `ROWS`, clamped at the edges; `at` not in `SPOTS` → `("right", "bottom")` whatever the step.

- [ ] **Step 1: failing tests**

```python
def test_set_project_theme_none_removes_the_key_and_keeps_comments(tmp_path, home):
    path = tmp_path / "projects.toml"
    path.write_text('# mine\n[[project]]\nname = "web"\npath = "/r/web"\ntheme = "Nord"  # dark\n\n# next\n[notify]\ndesktop = false\n')
    set_project_theme(path, Path("/r/web"), None)
    text = path.read_text()
    assert "theme" not in text and "# mine" in text and "# next\n[notify]" in text
    assert load(path).projects[0].theme is None
    set_project_theme(path, Path("/r/web"), None)  # already none: still fine
    assert path.read_text() == text

def test_step_spot_moves_and_stops_at_the_edges():
    assert step_spot(("center", "center"), 1, 0) == ("right", "center")
    assert step_spot(("center", "center"), 0, -1) == ("center", "top")
    assert step_spot(("right", "bottom"), 1, 1) == ("right", "bottom")
    assert step_spot(("left", "top"), -1, -1) == ("left", "top")
    assert step_spot((3, 1), -1, 0) == ("right", "bottom")  # custom: starts at bottom right
```

- [ ] **Step 2:** run them, expect failures (`TypeError` / `ImportError`).
- [ ] **Step 3:** implement both; update the comment above `SPOTS` (no longer "what ctrl+l steps through").
- [ ] **Step 4:** tests pass.
- [ ] **Step 5:** commit `feat(core): a project's theme can be removed; step_spot (#182)`.

### Task 2: source — save "none", say what "none" gets

**Files:** Modify `src/cardak/core/source.py`; Test `tests/test_source.py`

**Interfaces — Consumes:** Task 1's `set_project_theme`. **Produces:**
- `LiveSource.set_look(project_path, theme: str | None, background: bool, name=None, position=None)`: configured project → theme written/removed only when it differs; unconfigured → `add_project(theme=None …)` only when `theme` is a name, `background` is False or `position` is a non-default spot; otherwise nothing is written.
- `LiveSource.inherited_theme(project_path: Path) -> tuple[str | None, str] | None`: what the project gets with no scheme of its own, as `cardak.lua`'s `scheme_for` decides: `(name, scheme)` of the nearest *other* configured project with a theme whose path encloses `project_path` (longest path first), else `(None, <[defaults] theme>)`, else `None`.

- [ ] **Step 1: failing tests** — `test_set_look_none_removes_a_theme_and_writes_nothing_twice`, `test_set_look_none_on_an_unconfigured_repo_writes_only_for_an_art_change` (none + defaults: file unchanged; none + `background=False`: a project without `theme`), `test_inherited_theme` (nested in a themed project → `("outer", "Nord")`; top-level with `[defaults] theme = "Dracula"` → `(None, "Dracula")`; neither → `None`; the project's own theme is ignored).
- [ ] **Step 2:** run, fail. **Step 3:** implement. **Step 4:** pass.
- [ ] **Step 5:** commit `feat(core): set_look saves a project without a theme (#182)`.

### Task 3: preview without a scheme (navigate, Lua module 10)

**Files:** Modify `src/cardak/core/navigate.py`, `src/cardak/wezterm/cardak.lua`, `src/cardak/wezterm/generate.py` (`MODULE_VERSION = 10`), the test harness in `tests/test_lua.py`; Test `tests/test_navigate.py`, `tests/test_lua.py` (and whatever asserts the module number).

**Interfaces — Produces:**
- `preview_request(project_path, scheme: str | None, nonce, background=None, position=None)` and `Previewer.preview(project_path, scheme: str | None, …)`: `None` → `"scheme": false`; `""` clears as now.
- Lua: a request with `scheme == false` stores `{ scheme = false, path = req.path, background = …, position = … }`. `scheme_for` with such a preview skips the project whose `path` equals the preview's and returns the next enclosing themed project's scheme, else `default`. `background_for` / `position_for` already honor the preview. `""`, a missing or non-string non-false scheme still clears.

- [ ] **Step 1: failing tests**

```python
def test_preview_request_without_a_scheme_sends_false():
    payload = decode(preview_request(Path("/r/web"), None, "n1", position=("left", "top")))
    assert payload["scheme"] is False and payload["position"] == ["left", "top"]

# tests/test_lua.py
def test_a_preview_of_none_shows_the_project_without_its_scheme():
    wez = Wez(data(WEB)); w = wez.window(1, "/r/web")
    wez.preview(w, "/r/web", False)
    assert wez.scheme(w) is None
    wez.preview(w, "/r/web", "")
    assert wez.scheme(w) == "Nord"

def test_a_preview_of_none_in_a_nested_project_shows_the_outer_scheme(): ...   # inner /r/web/app themed Dracula inside WEB (Nord) → "Nord"
def test_a_preview_of_none_still_moves_the_art(): ...  # scheme False + background image + position on an unthemed project: layers placed at the previewed spot
```

(`decode` / `Wez.preview` as the neighbouring tests use them; extend `Wez.preview` to pass `False` through as JSON `false`.)

- [ ] **Step 2:** fail. **Step 3:** implement; bump the module's second line and `MODULE_VERSION`. **Step 4:** pass.
- [ ] **Step 5:** commit `feat(wezterm): preview a project without a scheme of its own, module 10 (#182)`.

### Task 4: backgrounds — the preview image for "none"

**Files:** Modify `src/cardak/core/backgrounds.py`; Test `tests/test_backgrounds.py`

**Interfaces — Produces:** `Tools.preview_image(project_path: Path, scheme: str | None) -> Image | None`; `None` draws in the default theme's foreground (`[defaults] theme` known to the scheme list), else `FALLBACK_COLOR`. A named scheme is unchanged.

- [ ] **Step 1:** failing test `test_tools_preview_image_for_no_scheme` beside `test_tools_for_the_picker`: with `[defaults] theme = "Nord"` the image for `None` equals the image for `"Nord"`; without a default it equals `render(art, FALLBACK_COLOR, …)`.
- [ ] **Steps 2–4:** fail, implement (factor the default lookup out of `_color`), pass.
- [ ] **Step 5:** commit `feat(core): the art's preview for a project without a scheme (#182)`.

### Task 5: `collectors/desktop.open_file`

**Files:** Create `src/cardak/collectors/desktop.py`, `tests/test_desktop.py`

**Interfaces — Produces:** `open_file(path: Path, wait_s: float = 3.0, run: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen) -> str | None`. Runs `["xdg-open", str(path)]` with `stdin/stdout/stderr=DEVNULL`, `start_new_session=True`. Returns `None` when it exits 0 within `wait_s` or is still running after it (left alone); `"xdg-open isn't installed"` on `FileNotFoundError`; `"xdg-open failed (exit N)"` on a non-zero exit; `str(e)` on another `OSError`. A path that doesn't exist is refused before running: `"no such file"`. Blocking (≤ `wait_s`): callers use a worker.

- [ ] **Step 1:** failing tests with a fake `run`: the argv and kwargs; exit 0 → `None`; exit 4 → `"xdg-open failed (exit 4)"`; `wait` raising `TimeoutExpired` → `None` and no kill; `FileNotFoundError` → the hint; a missing path → `"no such file"` and `run` not called (Review Focus 5).
- [ ] **Steps 2–4:** fail, implement, pass. `tests/test_layers.py` green.
- [ ] **Step 5:** commit `feat(collectors): open a file in the desktop's default app (#182)`.

### Task 6: the Art pane widget

**Files:** Create `src/cardak/ui/art_pane.py`, `tests/test_art_pane.py`

**Interfaces — Consumes:** `config.SPOTS`, `COLUMNS`, `ROWS`, `spot_name`, `step_spot`. **Produces:**

```python
class ArtPane(Widget, can_focus=True):
    class Changed(Message): ...          # the switch or the position changed
    class Open(Message):                 # e / c / a click on a path
        path: Path
    def __init__(self, *, on: bool, position: Position, art_file: Path | None,
                 config_file: Path | None, off_reason: str | None, configured: bool, id: str | None = None)
    on: bool                             # the switch now
    position: Position                   # where the dot is now
    moved: bool                          # position differs from the one it opened with by an arrow press
    def changes(self) -> list[str]       # of "art off" / "art on", "position": what differs from the opening values
```

Keys while focused: `space` toggle, `left/right/up/down` → `step_spot`, `r` reset position (and `moved = False`), `e` open art file, `c` open config. With `off_reason`: only the reason line, the config line and `c`. With the art off: grid dimmed, arrows ignored, hint "with the art on". Art line: path as a link when `art_file`; else "made when you save" (on), "made when the art is turned on" (off), "made once this is saved as a project" (not `configured`). Paths are shown with `~` (move `_tilde` here from `theme_picker.py`) and are clickable (`@click` actions or child `Static`s; the builder's choice). Header line above the files: "Plain text files, yours to edit:". The grid marks the dot with `●`, other spots `·`; a custom position has no dot and its `spot_name` + ", custom".

- [ ] **Step 1: failing tests** in a bare host app mounting one `ArtPane`:
  - arrows move and clamp, the name beside the grid follows (`"top left"` after `left`,`up`×2 from center);
  - `space` toggles, posts `Changed`; with the art off an arrow changes nothing;
  - custom `(3, 1)`: text has ", custom" and no `●`; first arrow → `("right", "bottom")`; `r` → `(3, 1)` and `moved is False`;
  - `changes()`: `[]`, `["position"]`, `["art off"]`, both; back to `[]` after `r` and a second `space`;
  - `e` posts `Open(art_file)`; without a file it posts nothing; `c` posts `Open(config_file)`;
  - `off_reason="background off for every project: …"`: the reason shown, no grid, `c` still posts;
  - a path `/r/[x]/projects.toml` renders literally (Review Focus 3);
  - `configured=False` → "made once this is saved as a project".
- [ ] **Steps 2–4:** fail, implement, pass.
- [ ] **Step 5:** commit `feat(ui): the Art pane, a switch, a position grid and its files (#182)`.

### Task 7: the dialog

**Files:** Modify `src/cardak/ui/theme_picker.py`; rewrite `tests/test_theme_picker.py`

**Interfaces — Consumes:** `ArtPane`; `ThemePreviewer.preview(path, scheme: str | None, …)`; `Backgrounds.preview_image(path, scheme: str | None)`. **Produces:**

```python
@dataclass(frozen=True)
class ThemeChoice:
    scheme: str | None            # None: no scheme of its own
    background: bool
    position: Position | None = None

ThemePickerScreen(project_name, project_path, current: str | None, load, previewer,
                  background=True, art_file=None, preview_image=None, backgrounds_off=None,
                  position=DEFAULT_POSITION,
                  inherits: tuple[str | None, str] | None = None,   # LiveSource.inherited_theme
                  configured: bool = True,
                  config_file: Path | None = None,
                  open_file: Callable[[Path], str | None] | None = None)
```

Decisions:
- Widget ids: `#picker`, `#picker-title`, `#unsaved`, `#schemes-pane` (holds `#scheme-find`, `#picker-status`, `#preview-note`, `#schemes`), `#art` (the `ArtPane`), `#keys`.
- `NONE_ID = "\0none"` is the "none" option's id. Its label: `none (as <name>: <scheme>)` / `none (the default: <scheme>)` / `none (WezTerm's own colors)` from `inherits`. It and, when `current` is not among the loaded schemes (loading, failed, unknown), a plain `"<current> (current)"` row are shown while the filter is empty; they are added in `on_mount`, before the load finishes. Highlight at open: `current`'s row, else "none".
- Bindings: `enter` → `save` (priority), `escape` → `cancel`, `tab` / `shift+tab` → `switch_pane` (priority; between `#scheme-find` and `#art`), `up` / `down` → `move` only when the filter has focus. `action_save`: highlighted row → its scheme (`None` for `NONE_ID`); no rows → the stripped filter text if any; else nothing.
- `OptionList.OptionSelected` (a click) saves that row, as now. `Input.Submitted` is no longer handled.
- `ArtPane.Changed` → update `#unsaved` and `_preview_now()`. Previews carry the position only when `art.moved`, the image via `preview_image(highlighted or None)`; with `backgrounds_off` the background argument is `None` as now.
- `#unsaved`: `"not saved: " + ", ".join(parts)` where parts are `"theme"` (highlighted row ≠ `current`) + `art.changes()`; empty string when none.
- `#keys`: `tab Art · enter Save · esc Cancel` with the filter focused, `tab Schemes · enter Save · esc Cancel` with `#art` focused.
- `_choose(None)`: clear the preview (`preview(path, "")`) before dismissing; a named scheme as today.
- `ArtPane.Open` → `run_worker(thread=True)` calling `open_file(path)`; then `notify(f"Opened {tilde}")` or `notify(f"Couldn't open {path}: {reason}", severity="error")` (full path). No `open_file` → the error notice "can't open files in this deck".
- Layout: `SIDE_BY_SIDE_AT` (screen columns; measure, start at 104): at or above, `#picker` is a `Horizontal` of the two panes; below, vertical with `#art` under the list. Set a `-stacked` class on mount and in `on_resize` from the **event's** width. The focused pane gets `border: round $accent`, the other `round $surface-lighten-2` (`:focus-within`).
- Title: `Theme and art: <escaped name>`.

- [ ] **Step 1: failing tests** (rewrite the file; keep `Recorder`, `Host`, `picker`, `ready`; port every existing scheme-list test unchanged where its keys still exist):
  - Enter saves from the filter, from `#art` (after `tab`), and after clicking the switch;
  - `tab`, arrows, Enter → `ThemeChoice("Nord", True, <spot>)`; move then `space` off → position dropped (`ThemeChoice("Nord", False)`);
  - `#keys` text per pane; `#unsaved` for theme / art off / position, combined, and empty again when undone;
  - "none": highlighted with `current=None`; label for each `inherits`; Enter → `ThemeChoice(None, True)`; highlighting it on a themed project previews `(PATH, None, …)`; saving it clears the preview;
  - **Review Focus 1:** a `load` that blocks (an `Event`) and one that raises: `current="Nord"`, `tab`, `left`, Enter → `ThemeChoice("Nord", True, ("center", "bottom"))`;
  - an unknown `current="Gone"` gets a `Gone (current)` row, highlighted;
  - **Review Focus 2:** type `tok`, `tab`, Enter → `"Tokyo Night"`; a filter matching nothing + Enter → the typed text; from `#art` too;
  - previews: arrow → `(PATH, "Nord", ART, <spot>)`; no arrow → no position in any call; Esc after an arrow → `(PATH, "")` last and `result is None`;
  - opening: `tab`, `c` → the opener got `config_file`, notice "Opened …"; an opener returning `"xdg-open isn't installed"` → an error notice containing the full path; no opener → the notice above;
  - `backgrounds_off`: reason shown, `tab` `c` still opens;
  - **Review Focus 3:** project name `we[b]` in the title renders literally;
  - **Review Focus 4:** `run_test(size=(60, 30))` mounts with `-stacked`; `(140, 40)` without; resizing between them flips it.
- [ ] **Step 2:** fail. **Step 3:** implement. **Step 4:** pass, plus the whole suite (the deck's own picker tests are fixed in Task 8; mark what breaks there).
- [ ] **Step 5:** commit `feat(ui): the theme picker becomes a two-pane theme and art dialog (#182)`.

### Task 8: the deck and the CLI

**Files:** Modify `src/cardak/ui/app.py` (`DeckApp.__init__`, `action_theme`), `src/cardak/cli.py` (`_deck`), `scripts/make_screens.py` if its "colors" slide needs a key; Test `tests/test_app.py` (and its `StubSource`), `tests/test_cli.py` if it asserts the deck's wiring.

**Interfaces — Consumes:** Tasks 2, 5, 7. **Produces:** `DeckApp(..., open_file: Callable[[Path], str | None] | None = None)`; `cli._deck` passes `collectors.desktop.open_file`.

Decisions:
- `action_theme` passes `inherits=self.source.inherited_theme(path)`, `configured=group.kind == "project"` (check the group kinds in `core/snapshot`), `config_file=self.source.config_file`, `open_file=self.open_file`.
- `done`: `choice is None` → return. `said` uses `"no theme"` for `None`. Nothing changed (`(scheme, background, moved) == (current, background, None)`) → clear preview, notice, **and** the `theme_status` hint. Otherwise `set_look(path, choice.scheme, …)` as now.
- The source protocol the deck types against gains `inherited_theme` and `config_file`; `StubSource` gets both.

- [ ] **Step 1: failing tests** — port `test_ctrl_l_in_the_picker_…` to `tab`, `left`×2, `up`×2, Enter → `positions == [("left", "top")]` and the notice `cardak: Nord, art top left`; port the "moved back writes nothing" test with `left`, `right`; new: "none" on a themed project → `source.themes[-1][1] is None`, notice `cardak: no theme`; `tab`, `c` calls the deck's `open_file` with the source's config file; an opener returning `"no such file"` → an error notice with the path (Review Focus 5); `theme_status` hint after a save that wrote nothing.
- [ ] **Steps 2–4:** fail, implement, pass; whole suite, ruff, pyright.
- [ ] **Step 5:** commit `feat(ui): the deck saves a project without a theme and opens its files (#182)`.

### Task 9: docs, screens, architecture, live check

**Files:** `AGENTS.md`, `README.md:78`, `CHANGELOG.md`, `site/assets/screens/*` (regenerated), `src/cardak/wezterm/cardak.lua:125` comment.

- [ ] **Step 1:** `AGENTS.md`: Layout (`ui/art_pane.py`, `collectors/desktop.py`); the `ctrl+l` paragraph under "Project background" rewritten for arrows, `r`, Enter, the "none" row and module 10; the read-only section gains "`e` / `c` open a file with `xdg-open`, nothing typed"; "One interface per external format" names `collectors/desktop.py` for `xdg-open`.
- [ ] **Step 2:** `README.md` line 78 for the new keys; `CHANGELOG.md` Unreleased: **Changed** the dialog and the removed `ctrl+b` / `ctrl+l` (and "re-run `cardak wezterm install`": module 10); **Added** the "none" row, opening the art file and `projects.toml`; **Fixed** Enter now shown and working from anywhere, art saved without a theme (#182).
- [ ] **Step 3:** `uv run scripts/make_screens.py`; check the "colors" slide shows the new dialog; `uv run pytest tests/test_make_screens.py`.
- [ ] **Step 4:** enola `generate_snapshot` + `diff_snapshot`; fix what improves the code, record accepted findings (Textual-called handlers as dead-code candidates, etc.) under "Architecture checks" as `#182`.
- [ ] **Step 5:** live check in a separate WezTerm (`--always-new-process`, its own `gui-sock-<pid>`, scratch `XDG_*` and `CARDAK_CONFIG`, a stub `xdg-open` first on `PATH`): "none" preview on a themed project, arrows moving the art, Enter saving, Esc restoring, `e`/`c` reaching the stub. Capture by `_NET_WM_PID`; never touch the owner's windows.
- [ ] **Step 6:** full checks; commit `docs: the theme and art dialog (#182)`.
