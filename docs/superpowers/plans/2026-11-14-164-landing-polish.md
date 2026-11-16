# #164 Landing page polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** the page of the spec: banner, centered header, wide video, tabbed install card,
centered footer, 18 slides.

**Architecture:** `scripts/build_site.py` fills `__RELEASED__` from CHANGELOG.md; `site/index.html`
gets the new layout and script; `scripts/make_screens.py` makes eleven more screens.

**Spec:** `docs/superpowers/specs/2026-11-14-164-landing-polish-design.md`

## Global Constraints

- Nothing loaded from elsewhere; relative paths; one inline script under 6000 characters, no `eval`.
- Invented data only (`/home/demo`); the leak test covers every slide.
- 44 px tap targets; no horizontal scroll at 360 px; light and dark.

## Review Focus

1. A version with no dated changelog heading (bumped, not cut): no banner, the build still passes.
2. JS off: both install commands visible, no tab buttons, the banner hidden.
3. The tagline at 768–1000 px: one line, not overflowing.
4. Keyboard: ←/→ on the tabs moves between them, and doesn't also move the carousel.
5. The new slides' aspect ratios in the fixed frame (short screens letterboxed, not stretched).

---

### Task 1: the release date

**Files:** `scripts/build_site.py`, `tests/test_build_site.py`

- [ ] Tests: `test_build_fills_the_release_date` (a CHANGELOG with `## 1.2.3 — 2026-01-02` →
  `data-released="2026-01-02"` filled), `test_no_dated_heading_means_no_date` (only `## Unreleased`
  → empty), `test_real_site_builds` still passes. `build(src, out, pyproject, changelog)`.
- [ ] Implement `released(changelog_text: str, version: str) -> str`; fill `__RELEASED__`.

### Task 2: the layout

**Files:** `site/index.html`, `tests/test_site.py`

- [ ] Tests first: banner (`hidden`, `data-released="__RELEASED__"`, link to
  `releases/tag/v__VERSION__`), the header centered, the tagline `nowrap` from 700 px,
  `#demo` before `#install` and not in a grid, the install tabs (tablist, two tabs, Full
  `aria-selected="true"`, panels with both commands, "Basic" and no "Lite"), the footer's two lines
  (Created by brlauuu, the changelog link), centered captions, the script under 6000.
- [ ] Implement; check live at 360, 390, 768, 1280, light and dark, and with JS off.

### Task 3: the screens

**Files:** `scripts/make_screens.py`, `tests/test_make_screens.py`, `site/index.html`,
`site/assets/screens/*.webp`

- [ ] Tests: `SLIDES` in `test_make_screens.py` gains each new deck slide with a marker
  (`providers`, `context`, `events`, `recap`, `cleanup`, `find`, `fold`, `add`, `docker`); the
  illustrations (`tabs`, `sounds`) are checked for their text; `test_carousel` lists all 18.
- [ ] Implement, run `uv run scripts/make_screens.py`, add the slides to the page.

### Task 4: docs

- [ ] CHANGELOG (Changed, #164), AGENTS.md's landing-page notes (the banner's date, the tabs,
  the screens list).
