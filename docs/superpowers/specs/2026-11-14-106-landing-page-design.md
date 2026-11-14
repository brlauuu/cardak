# #106: the landing page, redesigned, and its follow-ups

## Intent

The landing page (`site/index.html`, served at brlauuu.dev/cardak) says what cardak is in a
paragraph and a nine-item list. Its owner wants it to show it instead: a tagline, the demo video
with the install right beside it, and a carousel with one screen per feature. The same PR closes
the rest of #106, the small follow-ups from the page's first review.

## Goals

1. **Tagline**, under the wordmark: "Your control deck for multi-project agentic development."
   It replaces the current lede and the `<title>`, `description` and `og:description`.
2. **Video + install row.** The video (still the placeholder until #105 brings the recording)
   on the left, a panel on the right with:
   - a link to the GitHub repository;
   - **Full** install: `uv tool install 'cardak[all] @ git+https://github.com/brlauuu/cardak'`,
     one line on what it adds (the project background's art, Anthropic summaries);
   - **Lite** install: `uv tool install git+https://github.com/brlauuu/cardak`, one line on what
     it has (the deck, agent status, themes, resources, the phone and PM views);
   - each command with a **copy button** (inline JS, no external script; this also fixes the
     command cut off at 390 px);
   - the two setup steps (`cardak wezterm install` and the `require` line, `cardak hooks install`)
     and the README link, kept short.

   The needs line (Linux, WezTerm, uv, Claude Code) stays, in the panel. On narrow screens the
   row stacks: video, then the panel.
3. **Carousel**, under the row: seven slides, each a screen and a caption (a short title and one
   sentence). Order and content:
   1. **Every terminal, by project**: the deck in its wide layout, several projects, panes with
      state, RAM and graph, CPU, context and month columns.
   2. **What's waiting on you**: a waiting session and why: the side pane focused, or the `i`
      detail of a waiting session over the deck.
   3. **Why, in one line**: waiting sessions with their one-line model summaries.
   4. **What it costs, what it uses**: the history screen (`h`), a usage or cost figure and/or
      RAM/CPU per project and day; the top bar's spend and plan limits visible somewhere in the
      deck shots.
   5. **Your issues and PRs**: the PM view (`P`), todo / on it / done columns.
   6. **A color for each project**: the theme picker (`t`) over the deck, its scheme swatches.
   7. **On your phone**: the read-only web page at phone size.

   Each slide must show something the others don't. Behaviour: one slide at a time, CSS
   scroll-snap; swipe on touch, ← → buttons and dots on desktop, the keyboard's arrow keys when
   the carousel has focus. **No autoplay.** Works without JS as a horizontally scrollable strip.
4. **Drop the "What it does" list.** The captions are the feature list now.
5. **The rest of #106:**
   - `og:image` a 1200×630 preview (the wordmark and the tagline on Danube night), rendered by
     `scripts/make_brand_pngs.py` like the icons; `og:url` = `https://brlauuu.dev/cardak/`.
   - `pages.yml`: `cancel-in-progress: false`.
   - Action versions: every `uses:` in `pages.yml` and `test.yml` on its current latest major
     (checked with `gh api` at build time), together.
   - `site/assets/cardak-mark-light.svg`: dropped (nothing uses it; the placeholder and the
     preview image are dark). *My call.*
   - The phone page (`web/page.html`) gets a favicon and a home-screen icon, served by the
     daemon: unauthenticated `GET` routes for an SVG icon and the 180 px PNG, bundled in the
     package (in the wheel, not fetched), the CSP unchanged (it already allows `img-src 'self' data:`),
     `test_page_is_self_contained` updated to allow exactly those links. The icon files are the
     brand's; `tests/test_brand.py` covers the copies (or they are the same files).

## Screenshots: generated, from made-up data

- A script, `scripts/make_screens.py`, makes every slide's image. It runs the real deck headless
  (Textual `run_test` with a stub `DeckSource`, as the tests do) on **invented** projects,
  sessions, numbers, issues and costs, drives it with key presses to each screen, and saves
  Textual's SVG screenshot; the phone slide comes from the real `web/page.html` and
  `web/view.py` fed the same invented snapshot (a local stub server or the real one on a scratch
  port), in headless Chrome at 390×844. The data goes through cardak's own code (snapshot,
  views, widgets), so the images are what the deck really draws.
- Nothing from the machine: no real paths, names, repositories, sessions or user. Invented
  project names and home paths. It must not read `~/.config`, `~/.claude`, `~/.cache` or the
  real state; XDG dirs point at a scratch dir as in `tests/conftest.py`.
- The page loads nothing from elsewhere, and Textual's SVG fetches its font from a CDN. So the
  committed images are **PNG** rendered by headless Chrome from the SVG at 2× (the font may be
  fetched at build time, or a local monospace used; either way the committed file is raster).
  Keep each image modest (aim under ~300 KB); WebP is fine if Chrome makes it simply.
- The deck's own default look, not brand colors. Terminal size large enough to look like a real
  wide deck (around 160×40), the same for every deck slide.
- Re-run after a UI change: `uv run scripts/make_screens.py`. Committed outputs in
  `site/assets/screens/`.

## Constraints

- `site/index.html` stays plain HTML and CSS with relative paths; inline JS is allowed now (the
  carousel and copy buttons), still nothing loaded from elsewhere. `__VERSION__` stays in the
  footer. The page has no CSP of its own (Pages), but keep the JS small and without `eval`.
- Brand colors only as in AGENTS.md (night, paprika, linen; graphite only for the one-ink mark);
  `test_brand.py` stays green. Light and dark mode both work.
- Images have `alt` text, `width`/`height` to avoid layout shift, `loading="lazy"` after the
  first slide. Buttons are real `<button>`s with labels; the carousel is a labelled region.
- Phone width (390 px): no horizontal page scroll, commands readable or copyable.
- `tests/test_build_site.py` and whatever checks the site keep passing; extend them for what's
  testable without a browser (tagline present, every `src` under `assets/` exists, no external
  URLs besides links, the old list gone).
- The demo video's slot keeps its instructions (the comment) for #105; AGENTS.md's note about it
  updated if the markup changes.
- Live check: headless Chrome screenshots at 390×844 and 1280×800, light and dark.

## Non-goals

- Recording the video (#105).
- A brand-colored theme for the deck.
- Changing the README beyond what the dropped asset or the new script needs.

## Notes from the builder check

- Slide 1's wide layout shows the waiting pane too: keep slides distinct through the invented
  data each one gets.
- The `i` detail's transcript path, like everything else, points into the scratch dir.
- `test_brand.py` globs `site/assets/*.svg` only; the PNGs in `site/assets/screens/` are outside it.
- A new action major may change defaults: read each one's release notes while bumping.

## Left to the builder

The exact layout and spacing, caption wording within the meaning above, which screen best shows
slides 2–4, the invented data set, how the phone slide is framed in a landscape slide (one or
two phone shots side by side), and the icon routes' names.
