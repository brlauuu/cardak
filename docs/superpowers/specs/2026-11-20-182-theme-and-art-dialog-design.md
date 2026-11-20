# Theme and art dialog (#182)

## Intent

`t` on a project opens a dialog that began as a scheme chooser and had the art's switch
(`ctrl+b`, #9) and position (`ctrl+l`, #175) added to it. #182: after stepping the art
round with `ctrl+l` it is not clear how to save. Enter saves, but nothing on screen says so
(the dialog has no key line at all), Enter reads as "pick this scheme", and art cannot be
saved on a project without a theme. The owner asked for a redesign rather than a hint:
the look of a project is set in one dialog whose two halves, scheme and art, are equals,
and which shows that both are backed by plain text files that can be opened from it.

## Goals

- One dialog, two panes: **Schemes** (as today) and **Art** (switch, a visible 3×3 grid for
  the position, the files).
- Saving is one visible key (Enter) that works from anywhere in the dialog; what is changed
  and not yet saved is said on screen.
- Art can be saved without choosing a scheme, and a project's scheme can be removed
  (a "none" row).
- The art's text file and `projects.toml` are shown as paths that open in the desktop's
  default app, by click or key.

## Non-goals

- No screen for the global `[background]` settings (opacity, default position, enabled).
  `projects.toml` is one click away instead.
- No mouse clicks on the position grid.
- No numbered (custom) positions from the dialog: those stay a `projects.toml` matter.
- No change to what a saved project looks like in `projects.toml`, apart from a removed
  `theme` key.

## Anti-goals

- Anything changing on disk before Enter. Previews stay previews; Esc leaves no trace.
- Typing into a shell or opening a WezTerm tab to edit a file (that would need a confirm
  dialog under the repo's read-only rule).
- A key or control whose effect is only discoverable from the changelog.

## Approaches

- **A. A "Look" form, one row per setting, the scheme list as a sub-step.** Scales to more
  settings and makes the art independent of the theme, but changing a theme (the most common
  act) takes one more step, and it is the largest rewrite.
- **B. One dialog with two panes (picked by the owner).** Scheme picking stays as fast as it
  is; the position becomes a visible grid moved with arrows; Enter saves both. Costs: a wider
  dialog, focus to keep track of, and no natural place for a third group of settings.
- **C. Two screens, `t` for schemes and a new key for art.** Simplest, but a scheme and the
  art's place can't be judged together before saving, and it takes another deck key.

## The dialog

```
 Theme and art: web                              not saved: position
 ┌ Schemes ──────────────────┐ ┌ Art ──────────────────────────────┐
 │ filter schemes            │ │ [x] show art                space │
 │ > none (WezTerm's own)    │ │                                   │
 │   Builtin Dark    ● ● ● ● │ │   · · ·   bottom right            │
 │   Nord            ● ● ● ● │ │   · · ·   arrows move, r resets   │
 │   Tokyo Night     ● ● ● ● │ │   · · ●                           │
 │   …                       │ │                                   │
 │                           │ │ Plain text files, yours to edit:  │
 │                           │ │ e  art     ~/.config/cardak/logos/web.txt │
 │                           │ │ c  config  ~/.config/cardak/projects.toml │
 └───────────────────────────┘ └───────────────────────────────────┘
 tab Art · enter Save · esc Cancel
```

(The sketch is not to scale; the exact widths and wording are the builder's.)

### Layout

- Title: `Theme and art: <project name>`. On the same line, right-aligned, what differs from
  the values the dialog opened with: `not saved: theme, art off, position` (only the parts
  that differ; empty when nothing does).
- Side by side when the screen is wide enough for both panes without cutting a swatch label
  or a path; otherwise the Art pane goes below the list. The threshold is a constant the
  builder picks by measuring. On resize, use the event's width (`App.size` is stale there).
- The focused pane has the accent border, the other a muted one.
- The last line of the dialog names the keys, since the dialog covers the deck's footer
  (a line of its own, as the other modal dialogs have none of `WrappingFooter`; its keys
  differ per pane, which a footer of bindings can't say):
  `tab Art · enter Save · esc Cancel` in the Schemes pane, `tab Schemes · …` in the Art pane.
- The existing notes stay: "loading schemes…" / "Couldn't list schemes: …", and "No live
  preview: the deck isn't running inside WezTerm."

### Schemes pane

Behaves as today (filter field focused at open, typing filters, up/down move the list and
preview after `DEBOUNCE`, the current scheme marked), with one addition:

- **The "none" row.** First in the list, shown while the filter is empty. "None" means no
  scheme of the project's own, so the row says what the project then gets, the way
  `cardak.lua`'s `scheme_for` decides it: `none (as <enclosing project>: <scheme>)` for a
  project inside a themed one, else `none (the default: <name>)` when `[defaults] theme` is
  set, else `none (WezTerm's own colors)`. It is highlighted at open when the project has no
  theme.
- **The project's scheme is always a row**, and highlighted at open, even when the list does
  not have it: while the schemes load, after a failed load, or for a name WezTerm doesn't
  know. Then it is a plain row without swatches (`<name> (current)`), shown with the empty
  filter like "none". So Enter with nothing touched never changes or removes the theme
  (builder check: the first row would otherwise be highlighted, and that is now "none").
- Highlighting "none" previews the project without a scheme of its own (see Preview).
- With a filter that matches nothing, Enter saves the typed text as an exact scheme name, as
  today, from either pane. With the empty filter there is always a highlighted row.

### Art pane

One focusable widget; Tab moves focus between the filter field and it (both directions).
With it focused:

- **space** toggles the art (the checkbox can also be clicked).
- **arrows** move the dot one spot in the 3×3 grid of `config.SPOTS`, stopping at the edges.
  The spot's name (`spot_name`) is beside the grid. With the art off the grid is dimmed, the
  arrows do nothing and the hint reads "with the art on".
- **A custom position** (numbers in `projects.toml`) shows no dot and its description plus
  ", custom". The first arrow press puts the dot at bottom right; from then on arrows move it.
- **r** puts the position back to what it was when the dialog opened (so a stray arrow can't
  lose a custom position) and it no longer counts as moved.
- **e** opens the art file, **c** opens `projects.toml` (see Opening files). Both paths are
  also clickable from either pane.
- The art line keeps today's states: the path when the art was made (per
  `Backgrounds.art_file`), "made when you save" with the art on and no file yet, "made when
  the art is turned on" with it off. Only a path is a link, and `e` does nothing without one.
- **Art off for every project** (`Backgrounds.off_reason()`: `[background] enabled = false`,
  or the background extra missing): the pane shows that reason in place of the switch, grid
  and art line. The config line stays, and so does `c`.

`ctrl+b` and `ctrl+l` are removed.

### Saving

- **Enter saves from anywhere** in the dialog: a priority binding, so it works with the Art
  pane or a clicked checkbox focused. It dismisses with
  `ThemeChoice(scheme: str | None, background: bool, position: Position | None)`: the
  highlighted row's scheme (`None` for the "none" row), the switch, and the position only if
  it was moved and the art is on (`None`: leave it alone), as today.
- Clicking a scheme row still chooses it and saves (today's `OptionSelected`).
- **Esc** clears the preview and saves nothing.
- The deck (`DeckApp.action_theme`'s `done`) and `LiveSource.set_look` take `scheme=None`:
  - a configured project with a theme: its `theme` key is removed in place (tomlkit; a new
    `config.set_project_theme(..., None)` or equivalent), comments and layout kept;
  - a configured project without one: nothing to do for the theme;
  - a git repo that is not a project yet: with "none" and nothing else changed, nothing is
    written; with "none" and the art turned off or moved, it is added as a project without a
    `theme` key (`add_project` already allows that).
- The notice names what was saved: `web: Nord`, `web: no theme`, plus `, background off` and
  `, art top left` as today. Nothing changed: the preview is cleared and the notice still
  says the current state, as today.

### Preview

Today a preview needs a scheme name: `""` clears it. Two new cases need a preview with no
scheme of the project's own: the "none" row on a themed project, and moving the art on an
unthemed one.

- `cardak.lua` (**module 10**; bump `MODULE_VERSION`): a preview request whose `scheme` is
  JSON `false` means "this project without a scheme of its own": `scheme_for` skips the
  project at the preview's path and returns what the pane would then get (the nearest other
  enclosing project with a scheme, else the default), while the preview's `background` and
  `position` are honored as for a named scheme. `""` or a missing scheme still clears.
- Python: `ThemePreviewer.preview(path, scheme, …)` takes `scheme: str | None`; `None` sends
  `false`, `""` clears. `navigate.preview_request` likewise.
- An older installed module treats `false` as a clear: the preview shows the saved look, and
  the deck already tells the user to re-run `cardak wezterm install`.
- The art's preview image for "none" is drawn in the color the saved art of such a project
  gets today (`backgrounds._color` without the project's own theme: the default theme's
  foreground, else `FALLBACK_COLOR`; an enclosing project's scheme is not considered there
  today and stays so): `Backgrounds.preview_image(path, scheme: str | None)`.
- **A git repo that is not a project yet** has no art to preview until it is saved
  (`Tools._project` knows configured projects only; so it is today). Its switch and grid
  work and are saved, the scheme previews as now, and the art line reads "art: made once
  this is saved as a project" in place of "made when you save".
- On save with "none" (`_choose`): the preview is cleared, since after the reload the
  project has no scheme for a leftover preview to sit over.

### Opening files

- The deck gets `open_file: Callable[[Path], str | None]`, wired in `cli.py` the way
  `browse` is and handed to the dialog; tests pass a recorder. It returns `None` or the
  reason it failed.
- The real one runs `xdg-open <path>` detached (own session, no stdio), from a worker thread
  so a slow handler can't freeze the deck. `xdg-open` missing, or exiting non-zero within a
  few seconds, is a reason; one still running after that counts as opened and is left alone.
- The older-module hint (`theme_status`) is also shown after a save that wrote nothing, so
  a "none" preview that an old module ignored is explained.
- Success: a notice `Opened ~/.config/cardak/logos/web.txt`. Failure: an error notice with
  the reason and the full path, so it can be copied.
- The config path is the file the deck loaded (`LiveSource.config_file`), shown with `~`.
- The dialog stays open with its unsaved state. Nothing else is needed for the edits to
  show: the deck re-reads `projects.toml` when it changes, and the theme writer redraws art
  whose text changed (`art_stamp`). A later Enter in the dialog writes only the keys the
  dialog changed, over whatever the editor wrote to those keys.
- Where the function lives is the builder's call within the layering rules (`ui` must not
  grow a second place that runs external programs ad hoc; one small module).

## Constraints

- Layering and "one interface per external format" as in `AGENTS.md`; `tests/test_layers.py`
  must stay green. The dialog still gets everything through `ThemePreviewer`, `Backgrounds`
  and callables, importing nothing from `cardak.wezterm` or `cardak.llm`.
- Config writes on the UI thread only re-read the config; art is made by the next poll (#129).
- The dialog text is escaped where it shows names and paths (`rich.markup.escape`), as now.
- Coverage floor 80 %, `ruff`, `pyright`; Lua behavior is tested through `tests/test_lua.py`.
- enola: pin before, diff after; weigh findings, fix what improves the code, record the
  accepted ones in `AGENTS.md`.

## Tests (at least)

- Enter saves from the filter field, the Art pane and a clicked checkbox.
- The key line and the "not saved" line for each kind of change, and back to empty when a
  change is undone.
- Arrows: each direction, the edges, art off, a custom position's first press, `r`.
- "none": highlighted for an unthemed project; saves `scheme=None`; removes a theme in place
  keeping comments; an unconfigured repo with "none" writes nothing unless the art changed.
- Preview requests for "none" (`false`) with background and position; Lua: `false` gives the
  default scheme with the previewed art and position; `""` still clears.
- Opening: `e` and `c` call the opener with the right paths; no art file → `e` does nothing;
  a failure shows the reason and the path; backgrounds off → `c` still works.
- Stacked layout below the threshold.
- The existing picker tests are rewritten for the new keys, not dropped.

## Also in the change

- `AGENTS.md`: the picker paragraphs (#175's `ctrl+l` text), module 10, the opener, layout.
- `CHANGELOG.md` under Unreleased: Changed (the dialog, old keys gone), Added (none row,
  opening files), Fixed (#182).
- The landing page's screens (`scripts/make_screens.py`) and any site or README text that
  names `ctrl+b` / `ctrl+l`.
- A live check in a separate WezTerm (`--always-new-process`, scratch XDG dirs), never the
  owner's windows; `xdg-open` replaced by a stub there.

## Left to the builder

Widths and the stacking threshold; the grid's glyphs and dimming; the exact wording of hints
and notices; whether the Art pane is one widget or a container with non-focusable children;
where the opener lives; how long the opener waits for an exit code.
