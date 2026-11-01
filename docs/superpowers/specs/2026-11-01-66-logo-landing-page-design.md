# cardak logo and landing page (#66)

## Intent

cardak has no face: no logo, no favicon, no page to send someone to. #66 gives it both:
a logo used everywhere the name appears as an image, and a landing page at
**brlauuu.dev/cardak**. Someone who lands there should leave knowing **what cardak does**
and **how to get it**.

## Goals

1. A logo: the mark (a stylized Č), a wordmark built from it, a favicon, as SVG files, plus
   the PNG sizes favicons and listings need.
2. A one-page site in this repository, published by GitHub Pages, reachable at
   `https://brlauuu.dev/cardak` (and `/cardak/`).
3. The README shows the wordmark at the top.
4. The site at brlauuu.dev lists cardak among its projects, with the logo.

## Non-goals

- The deck and the phone page (`web/page.html`) keep their current look; no favicon for the
  web view in this issue (its CSP and `test_page_is_self_contained` make it its own change).
- No PyPI release, no install script: installing stays `uv tool install` from git.
- No analytics, no cookies, no third-party requests from the page (no web fonts, no CDN).
- #9 (a faint logo as a terminal background) is separate; this logo may feed it later.
- The demo video itself: the user records it. This issue ships a placeholder.

## Anti-goals

- A real recording or screenshot of the user's own deck (project names, paths, sessions) on a
  public page. Anything shown uses made-up projects (webshop, api-gw, notes), as the docs do.
- The stylized Č set in plain text. In running text, docs, commit messages and code the name
  is a normal "cardak". The mark stands in for the Č **only** where the name is drawn as an image.

## The logo (decided by the user)

### Shape

A **C** whose **háček cuts a notch into its top**, with a clear gap around it, so it's always
visible which part is the háček and which is the C. Monoline, round caps. Reference
geometry, in a 100 × 100 box (stroke centerlines):

- C: `M69.8 38.2 A28 28 0 1 0 69.8 77.8`, stroke width 12, round caps (a circle of radius 28
  around (50, 58), open ±45° on the right).
- Háček: `M38 14 L50 33 L62 14`, stroke width 7, round caps and join.
- The cut: the háček path at stroke width 16 removed from the C (an SVG `<mask>`), so the gap
  is **transparent**, not painted in a background color. The mark works on any background.

The builder may refine curves and spacing by eye; the shape and proportions stay.

### Colors: three, shared by both versions

| Name | Hex |
|---|---|
| Danube night | `#0f2a44` |
| Paprika red | `#c8352b` |
| Linen | `#f6efe2` |

| | Dark-theme version | Light-theme version |
|---|---|---|
| C and wordmark letters | Linen | Linen |
| Háček | Paprika red | Danube night |
| Tile background (favicon, app icon) | Danube night | Paprika red |

The user asked explicitly that both versions use the very same shades (no brighter paprika
for the dark one). Paprika on Danube night is the lowest-contrast pair; the transparent cut
is what keeps the háček readable at 16 px.

On a page background that is not one of the tiles (the README on GitHub, the landing page),
the letters need contrast: the light-theme wordmark on a light page uses **Danube night**
letters with a **paprika** háček, and the dark-theme wordmark on a dark page uses **linen**
letters with a **paprika** háček. *(Builder's call, flagged for the user: these "on page"
variants follow from the palette; the tile versions above are what the user approved.)*

### Wordmark

The mark **is** the Č of the name: `Č` + `ardak`, lowercase. "ardak" is **drawn**, not set in
a font, with the mark's stroke (width 12, round caps) on the same x-height (the C's circle),
so the word reads as one drawing. Reference geometry in a 460 × 100 box, after the mark at
x 0–100:

```
a: circle (128, 58) r 28; stem M156 30 V86
r: M186 86 V30 M186 58 A28 28 0 0 1 214 30
d: circle (270, 58) r 28; stem M298 6 V86
a: circle (356, 58) r 28; stem M384 30 V86
k: M414 6 V86 M444 30 L414 62 M424 52 L448 86
```

Only the Č has a háček. The builder may adjust spacing and the k's arms by eye.

### Files

Source SVGs are hand-written, kept small and readable (no editor metadata), in `site/assets/`
so the page and the README use the same files:

| File | What |
|---|---|
| `cardak-mark.svg` | the mark alone, transparent background (see the note below) |
| `cardak-wordmark-light.svg` | wordmark for light pages: Danube night letters, paprika háček |
| `cardak-wordmark-dark.svg` | wordmark for dark pages: linen letters, paprika háček |
| `cardak-icon.svg` | the favicon: the mark on a rounded tile; dark version, and the light version through `prefers-color-scheme` inside the SVG |
| `cardak-icon-32.png`, `cardak-icon-180.png` | PNG favicon and apple-touch icon (dark tile) |
| `cardak-mark-mono.svg`, `cardak-mark-mono-512.png` | the mark in one ink, graphite `#17181c`, transparent background and transparent cut, no tile: for the project list on brlauuu.dev |

Note on `cardak-mark.svg`: one transparent mark needs one color pair. Use the light-page pair
(Danube night C, paprika háček) or ship `-light` / `-dark` variants like the wordmark; the
builder picks, as long as every place that shows it has contrast.

PNGs are generated from the SVGs by a script in `scripts/` (like `scripts/make_sounds.py`),
committed, and regenerated when the SVGs change. Any rasterizer available on the dev machine
(e.g. `rsvg-convert` or headless Chrome) is fine; it runs by hand, not in CI.

## The landing page (decided by the user, order included)

Top to bottom:

1. **The wordmark**, large.
2. **What cardak does**: one or two sentences. Start from the README's opening paragraph:
   a project-centric deck for WezTerm panes and Claude Code sessions.
3. **Main features**, as a list. Each one short, written for someone deciding whether to try
   it. Drawn from the README and CHANGELOG, roughly: panes grouped by project and a jump to
   any pane; a color scheme and tab titles per project; which Claude Code session is working,
   waiting on you or idle, with a notification and a one-line summary of what it needs; CPU and
   RAM per pane and project, with alerts and history; spend at API prices, usage trends and
   context per session; plan limits; checkpoint and recap; the PM view (issues and PRs per
   project); the read-only phone page. The builder picks the wording and how many make the
   cut; the page shouldn't become the README.
4. **A video of cardak in use: a placeholder for now.** A 16:9 frame in the brand colors
   saying the video is coming. Swapping in the real one must be a small, documented edit:
   put the file in `site/assets/` and replace the placeholder element with a `<video>` (the
   element and its attributes are written out in a comment next to the placeholder).
5. **How to get it** (builder's addition from "know how to get it"): the requirements (Linux,
   WezTerm, Claude Code for the agent features), `uv tool install git+https://github.com/brlauuu/cardak`,
   then `cardak`, and a link to the README's install and setup sections. Copy buttons are fine
   if they need no external script.
6. Footer: a GitHub link and the current version.

### Look

- Follows the visitor's light or dark setting (`prefers-color-scheme`), with the matching
  wordmark (`<picture>` or two `<img>` toggled by CSS).
- Colors from the palette: Danube night, paprika red, linen, plus neutrals as needed.
- System font stack, no web fonts. Readable at 390 px wide and at desktop width.
- Plain HTML and CSS, no build step, no JavaScript needed to read it.
- `<title>`, description and Open Graph tags; favicon links to the files above.

### Values filled in at deploy

The page shows the version. It lives in `pyproject.toml` only (AGENTS.md), so the page holds
a placeholder (`__VERSION__`) that the deploy workflow fills from `pyproject.toml`, and the
workflow fails if any `__NAME__` placeholder is left in the output.

## Publishing

### In this repository

- `site/index.html` and `site/assets/`.
- `.github/workflows/pages.yml`: on pushes to `main` that touch `site/**`, `pyproject.toml` or
  the workflow, and by hand. No tag trigger: the default `github-pages` environment accepts
  deployments from the default branch only, and a version bump reaches `main` through the
  release PR anyway (builder's call). It copies `site/` to `_site/`, fills the version,
  checks for leftover placeholders, and deploys with `actions/upload-pages-artifact` and
  `actions/deploy-pages`. Permissions: `contents: read`, `pages: write`, `id-token: write`.
- **GitHub Pages must be enabled for the repository with "GitHub Actions" as the source.**
  It is off today. Enabling it changes repository settings: ask the user first, then
  `gh api -X POST repos/brlauuu/cardak/pages -f build_type=workflow` (or they do it in the
  settings page).
- **Set the repository's "About → Website" to `https://brlauuu.dev/cardak/`** (the user
  approved this on 2026-11-01): `gh repo edit brlauuu/cardak --homepage https://brlauuu.dev/cardak/`,
  once the page is live behind the proxy. It shows on the GitHub repo page and is where the
  brlauuu.dev project card's website icon links.
- The page is then at `https://brlauuu.github.io/cardak/`. All paths in the page are
  **relative** (`assets/…`), so it works there and behind the proxy.
- `README.md`: the wordmark at the top (`<picture>` with the dark and light SVGs), linking to
  the landing page.
- `AGENTS.md`: `site/` and the workflow in the layout, and a line on the logo rule (stylized Č
  only in images; plain "cardak" in text). `CHANGELOG.md`: a line under Unreleased.
- Committed content follows the public-repo rule: no personal details, no names of the
  user's other projects.

### In the site repository (brlauuu.github.io), a separate PR

That repository serves brlauuu.dev from Vercel and already proxies another project page the
same way. Following its conventions (its own AGENTS.md):

- `vercel.json`: a permanent redirect `/cardak` → `/cardak/`, and a rewrite
  `/cardak/(.*)` → `https://brlauuu.github.io/cardak/$1`. The trailing-slash redirect matters:
  without it, relative `assets/…` paths resolve against `/` and break.
- `_data/projects.yml`: a cardak entry with `logo_url` pointing at the raw
  `site/assets/cardak-mark-mono-512.png` on `main`. **Decided by the user:** the list gets the
  one-ink mark, not the colored tile. That list styles every logo as monochrome (multiplied on
  light, inverted and screened on dark), which would turn the tile pale with a cyan háček; a
  one-ink mark on transparent comes out dark on light and light on dark, like the other cards.
  The site's CSS and card template stay unchanged. Use `logo_scale` if the mark looks small.
- No `website_fallback`: the card's website link comes from the repository's "About → Website"
  field (below).

Order: the cardak PR merges and Pages deploys first, then the site PR, so the rewrite never
points at nothing.

## Verification

- The page in headless Chrome at 390 × 844 and 1180 × 820, in light and dark, from the local
  files and then from `https://brlauuu.dev/cardak` once both PRs are live.
- The favicon at 16 and 32 px on both a light and a dark browser tab strip.
- The README rendered on GitHub in light and dark.
- `uv run pytest` and the rest of CI unchanged and green (nothing under `src/` changes; the
  wheel must not pick up `site/`).

## Left to the builder

The exact copy, the feature list's wording and length, the page's spacing and type scale,
the video placeholder's look, small refinements of the glyphs, the PNG script's tool, and
whether `cardak-mark.svg` is one file or a light/dark pair.

## Calls made in writing this up (for the user to check)

- The "on page" wordmark colors (Danube night letters on light pages, linen on dark, paprika
  háček on both), which follow from the approved palette.
- A "How to get it" section and a footer with the GitHub link and version, from "they need to
  know how to get it".
- The page fills in only the version at deploy: no star or fork counts, no release banner.
- No tag trigger for the Pages workflow.
- PNGs generated by a hand-run script and committed; no favicon for the deck's phone page yet.
