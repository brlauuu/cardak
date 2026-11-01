# A faint project name or logo behind the terminal (#9) — design

## Intent

The user wants every WezTerm window to say **very clearly which project they're working on**, next
to the tab title (`<project> · <pane title>`, #31). A small tab title is easy to miss; a faint mark
behind the terminal's text is not. The issue's comment settles what the mark is: by default the
project's name in ASCII letters, with an ASCII logo when the project has a logo.

Success: switching to a window (or a tab or pane in another project) shows that project's ASCII
art in the bottom-right corner of the window, faint enough that the text over it reads as before.

## Goals

- **The look** (picked from mockups): the art sits in the **bottom-right corner** of the window,
  watermark-sized, at **6% opacity**, drawn in the scheme's foreground color so it suits whichever
  scheme the project has (for a project without one, see "Settled after the builder check").
- **It follows the active pane**, exactly as the color scheme does now (`sync` in `cardak.lua`):
  switching tab, pane or window shows that pane's project. A pane in no project shows no art.
- **Generated once, per project.** When a project is added, cardak makes its ASCII art one time:
  - the project has a logo → the logo image converted to ASCII characters (light and dark areas
    mapped to sparser and denser characters);
  - no logo → the project's name spelled out in large ASCII letters (figlet-style, as in the
    mockup: five rows for "cardak").
- **The art is the user's file.** It's saved as plain text, `~/.config/cardak/logos/<file>.txt`
  (my call on the folder; the name is derived from the project, see the builder's part). cardak
  never overwrites it and offers **no regeneration**. To replace it, the user pastes any ASCII art
  into that file; the help says so (see "Telling the user").
- **Projects that already exist** (configured before this version) get their art the first time a
  cardak with this feature runs, then never again, like new ones. Projects added by hand in
  `projects.toml` count as added when cardak first sees them.
- **Deleted file → no background** for that project; cardak does not make a new one (my call;
  played back to the user and not objected to). The once-only rule needs a record of which projects
  were generated, separate from the file's existence.
- **On/off per project in the deck**, in the theme picker next to the scheme list: a switch for that
  project's background, previewed live while the picker is open, saved to the project's entry in
  `projects.toml` with the scheme. The picker also shows where the project's ASCII file is (the
  "how to replace it" hint).
- **Off for every project**: one setting in `projects.toml` (no deck switch for it; my call).
  Opacity is one global setting too, default 6% (my call: not per project).

## Non-goals

- Regenerating art from cardak, in any form (no key, no command, no flag).
- Per-pane backgrounds: WezTerm draws one background per window, so a split window shows the
  focused pane's project, like the scheme.
- Choosing position or size in the deck. Position is fixed bottom-right; opacity is the only knob.
- Fetching logos from the network (GitHub avatars, websites). Only files in the project's own tree.
- A light-scheme special case beyond drawing in the scheme's own foreground color.

## Anti-goals

- **Hurting readability.** If the art makes text harder to read, it failed even when it works.
- **Touching what's not cardak's.** `wezterm.lua` stays unread and unedited (AGENTS.md). If the
  user's own config sets a background (`background` or `window_background_image`), cardak leaves
  that window's background alone, as it leaves a scheme it didn't set.
- **Slowing the hook.** `cardak hook` and `cardak statusline` import nothing new (the existing
  import guards in `tests/test_cli.py` must still pass); image libraries are imported only where
  art or images are made.
- **Overwriting the user's art.** Once the text file exists, only the user changes it.

## Constraints (from the project and measured facts)

- **WezTerm can't draw text as a background, only images** (WezTerm 20240203, the supported
  version). So there are two artifacts per project:
  1. the **text** file (the user's, in `~/.config/cardak/logos/`), and
  2. an **image** rendered from it (cardak's, a cache: e.g. `~/.cache/cardak/backgrounds/`),
     redrawn whenever the text or the colors it's drawn in change. Redrawing the image from edited
     text is not "regeneration" of the logo.
- The image reaches WezTerm the way schemes do: `cardak_projects.lua` (written by the deck on every
  config change and by `cardak themes apply`) gains each project's image path; `cardak.lua` sets it
  as a window background through `window:set_config_overrides`, in `sync`, next to the scheme.
  WezTerm's `background` layers (20220807+) support `horizontal_align = 'Right'`,
  `vertical_align = 'Bottom'`, no repeat, a size and `opacity`. Setting `background` replaces the
  plain background, so the layer list needs the scheme's background color as its first layer.
  This needs a spike against a test GUI (`--always-new-process`, its own `gui-sock-<pid>`, per
  AGENTS.md) before it's planned in detail.
- `cardak.lua` gains a feature the Python side relies on → bump `MODULE_VERSION` (to 5), so users
  with module 4 are told to re-run `cardak wezterm install`.
- **Logo search** (the user allowed cardak to search the project's repo, once, when the project is
  added). Order, my call from the four configured repos:
  1. the first local image in the README (`<img src>` or `![](…)`, relative path);
  2. git-tracked image files (`svg`, `png`, `jpg`, `webp`, `ico`) whose name contains `logo`, then
     `mark` / `wordmark`, then `icon`;
  3. among equals: prefer a `dark` variant (terminals are mostly dark), then the larger image.
  Skip vendor boilerplate (`next.svg`, `vercel.svg`, `file.svg`, `globe.svg`, `window.svg`, …) and
  anything under screenshot/design folders. Tracked files only (`git ls-files`, read-only, through
  `collectors/git.py`). Not a git repo → only the README step, then the name. What the four repos
  would give: podlog its dark-theme logo, cardak a mark, flatpare `flatpare_logo*.svg`, schnj its
  Expo app icon. An odd pick only costs the user a paste.
- **New dependencies** (my call, accepted by the user): **Pillow** (image decoding, ASCII
  conversion and drawing the text into the PNG) and **resvg's Python wheel** (most of the logos are
  SVG and nothing on the machine rasterizes SVG: no rsvg-convert, no ImageMagick). Check the
  package name, its wheel for Linux x86_64 and its API before planning.
- **Size cap**: logo art at most about 40 columns × 16 rows (my call), name art as the font gives
  it. The rendered image's size in the window is the builder's, judged against the mockup
  (watermark in the corner, not filling the window).
- Layering (AGENTS.md): Lua and its generator stay in `wezterm/`; file reading of the project's
  tree in `collectors/`; the ASCII generation and the "generated once" record in `core/` (or a new
  module there); the picker switch in `ui/theme_picker.py`. `wezterm/` imports nothing from cardak
  except where `generate.py` already does.
- Every file cardak writes goes into AGENTS.md's table of written files, with the same rules:
  atomic, only when content changes, and the text file never overwritten.

## Settled after the builder check (all my calls)

- **Which projects get art: configured ones only** (`projects.toml`), as the user said "every
  project I add". A git root the deck shows but nobody added gets no art and no generation record.
  Its picker can still turn the switch on: saving then adds the project, as saving a scheme
  already does (`LiveSource.set_theme`), and that is the moment it's "added".
- **Color without a scheme.** The art is drawn in the project's scheme foreground, else the
  `[defaults] theme` scheme's, else a light gray (`#c0c0c0`) that assumes a dark terminal; cardak
  can't learn a scheme set in `wezterm.lua`. All four configured projects have schemes today.
- **The zsh tint.** The user doesn't use it (not sourced in `~/.zshrc`, no `tint` keys). The spike
  checks whether background layers hide a pane's OSC 11 tint. If they do, the README says the
  background covers the tint and how to turn the background off; no merging of the two.
- **SVG.** `resvg-py` 0.5.0 has a manylinux x86_64 wheel on PyPI (checked 2026-11-01); confirm
  its license and API before adding it. If it's unusable, SVG candidates are skipped in the logo
  search (PNG and others still count) rather than adding a system-library dependency.
- **Saving the switch.** The switch and the scheme are saved together on Enter, as the scheme is
  now (Enter on the current scheme keeps it); Esc discards both and clears both previews. One
  preview message carries scheme and background together.
- **The user's own background.** `apply(config, { background = false })` turns cardak's off, like
  `tab_titles = false`; and if the `config` passed to `apply` already has `background` or
  `window_background_image` set, cardak sets none. A background set after `apply` is the user's
  to opt out of with the flag; the README says so.

## Telling the user

- README: a section on the background, where the text file lives, that it is generated once, how
  to replace it (paste your own ASCII art into the file), how to turn it off per project and for
  all.
- The theme picker: the switch, and the path of the text file under it.
- CHANGELOG under Unreleased → Added.

## Left to the builder

- The config keys' names and shapes (a per-project key like `background = false`, a global
  `[background]` table with `enabled` and `opacity`, or similar), following how `theme` and
  `[notify]` are read and written (`tomlkit` for edits in place).
- The text file's naming (from the project's name, made safe for a file name, unique among
  projects) and the "generated once" record's form and location (state dir is the natural home).
- The figlet-style font: bundle a small one (license-compatible) or write the glyphs; and the
  monospace font used to draw text into the PNG (a system one via fontconfig, or bundled).
- The ASCII conversion details (character ramp, aspect correction, cropping transparent margins,
  a dark-on-light logo inverted so the mark is the dense part).
- How the preview in the picker carries the background (extend `cardak_preview` or a second user
  var), and where image rendering for a previewed scheme happens.
- Whether generation runs in the deck's config-change path or a worker, as long as the deck stays
  responsive and generation happens exactly once per project.
