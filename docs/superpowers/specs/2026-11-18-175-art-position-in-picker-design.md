# #175: placing the background art from the theme picker

## Problem
A project's art is placed by `background_position` in `projects.toml` (#132, #168), and nothing in
the deck says so. The theme picker (`t`) has the art's on/off switch (`ctrl+b`) and its file, but
not where it sits.

## Decision (asked 2026-11-18)
A shortcut with a live preview: **`ctrl+l`** in the picker moves the art through the nine named
spots, the project's windows show each one, and Enter saves it with the scheme.

## Behaviour
- A line under the switch: `position: bottom right (ctrl+l to move)`. It names the position the
  windows show now: the project's own `background_position`, else `[background] position`, else
  bottom right. Numbers are worded too (`top, 3 cells from the right`), marked `custom`.
- `ctrl+l` steps through `SPOTS`, in reading order: top left, top center, top right, center left,
  center, center right, bottom left, bottom center, bottom right, then round again. From a named
  spot it takes the next; from a custom one, the first. A custom position is not in the cycle:
  Escape keeps it.
- Each step previews the highlighted scheme with the art at the new spot. With the switch off,
  `ctrl+l` does nothing (there is no art to move); with backgrounds off for every project (#126,
  #131) the line isn't shown and the key does nothing.
- Enter saves the position only when `ctrl+l` changed it: a spot equal to the default (the
  `[background] position`, else bottom right) removes the project's key, so the project follows the
  default again; any other writes `background_position = ["<column>", "<row>"]`. A project being
  added (a git repo themed for the first time) gets the key in its new `[[project]]` block.
- The deck's notice names a moved position: `cardak: Nord, art top left`.

## Wiring
- `config.SPOTS`, `config.spot_name(position)`, `config.set_project_position(path, project, at)`
  (None removes the key, via `_edit_project`), and `add_project(..., position=)`.
- `LiveSource.background_position(project) -> (own, default)`, and `set_look(..., position=)` with
  a sentinel for "unchanged".
- `ThemeChoice.position`: the chosen position, or `None` when `ctrl+l` wasn't pressed.
- `navigate.preview_request(..., position=)` adds `"position": [cols, rows]`; `Previewer.preview`
  and the `ThemePreviewer` protocol pass it on.
- `cardak.lua` (module **9**): a preview's `position` wins over the project's in `position_for`.
  An older module ignores it: the art moves only once saved, and the deck already tells users with
  an older module to re-run `cardak wezterm install`.

## Tests
Picker: the line's wording (own, default, custom, off), the cycle and its wrap, the preview carrying
the position, `ThemeChoice.position` set only after `ctrl+l`, Escape. Config: writing, replacing and
removing the key with comments kept; `add_project` with a position. Source: unchanged position
writes nothing; the default removes the key. Lua: a preview's position places the art and clearing
it restores the saved one. navigate: the request's JSON. App: the notice.
