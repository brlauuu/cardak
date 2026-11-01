# Logo and landing page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give cardak a logo (SVG mark, wordmark, favicon, PNGs) and a one-page site published by GitHub Pages and served at brlauuu.dev/cardak.

**Architecture:** Hand-written SVGs and a static `site/index.html` live in `site/`. A small stdlib script (`scripts/build_site.py`) copies `site/` to `_site/` and fills the version from `pyproject.toml`. A Pages workflow runs it and deploys. A second hand-run script rasterizes the PNGs with headless Chrome. The proxy and the project-list entry are a separate PR in the site repository, made after Pages is live.

**Tech Stack:** SVG, HTML, CSS (no JS needed to read the page), Python 3.13 stdlib (`tomllib`, `xml.etree`, `shutil`), pytest, GitHub Actions (`actions/upload-pages-artifact@v3`, `actions/deploy-pages@v4`, `actions/configure-pages@v5`), headless `google-chrome` for the PNGs.

**Spec:** `docs/superpowers/specs/2026-11-01-66-logo-landing-page-design.md`

## Global Constraints

- Palette, exactly: Danube night `#0f2a44`, Paprika red `#c8352b`, Linen `#f6efe2`; the one-ink listing mark is graphite `#17181c`. No other colors in any logo file.
- Mark geometry (100 × 100): C `M69.8 38.2 A28 28 0 1 0 69.8 77.8`, stroke 12, round caps; háček `M38 14 L50 33 L62 14`, stroke 7, round caps and join; the cut is the háček path at stroke 16 in a `<mask>`, so the gap is transparent.
- Wordmark (460 × 100): the mark at x 0–100, then `a` circle (128,58) r28 + `M156 30 V86`; `r` `M186 86 V30 M186 58 A28 28 0 0 1 214 30`; `d` circle (270,58) r28 + `M298 6 V86`; `a` circle (356,58) r28 + `M384 30 V86`; `k` `M414 6 V86 M444 30 L414 62 M424 52 L448 86`; stroke 12, round caps and joins. Only the Č has a háček.
- Colors per version: tile dark = Danube night background, linen C, paprika háček; tile light = paprika background, linen C, Danube night háček. On a plain page: light = Danube night letters + paprika háček, dark = linen letters + paprika háček.
- The stylized Č appears only in images. Text, docs, commits and code say "cardak".
- Page: no third-party requests (no fonts, CDN, analytics), all asset paths relative (`assets/…`), follows `prefers-color-scheme`, readable at 390 px and desktop width.
- The version lives in `pyproject.toml` only; the page carries `__VERSION__`, filled at deploy; leftover `__NAME__` placeholders fail the build.
- Public repo: no personal details, home paths, or names of the user's other projects in committed files or commit messages. Made-up example projects (webshop, api-gw, notes) only.
- Nothing under `src/` changes; the wheel and sdist must not contain `site/`.
- Repository settings (enabling Pages, the About website) change only at the steps that say so; the user approved both on 2026-11-01.

## Review Focus

1. **Visiting `/cardak` without a trailing slash** — relative `assets/…` paths would resolve against `/` and every image breaks; the site repo's redirect `/cardak` → `/cardak/` must exist before the rewrite is relied on (Task 6 checks it live).
2. **A dark-mode visitor** — the wordmark must switch to the linen version and the favicon to the dark tile; a light-only check misses it (Task 3's test pins the `prefers-color-scheme` wiring, Task 6 checks it in Chrome).
3. **The video placeholder left in place for weeks** — it must look intentional, not broken, and swapping in the real video must not need anything beyond the commented `<video>` element (Task 3's test pins the comment).
4. **A version bump without a site change** — the page must still show the new version; the workflow's path filter includes `pyproject.toml` (Task 4's test pins it).
5. **A logo file that picks up a stray color or editor metadata after a later hand edit** — the palette test fails on it (Task 1).

---

### Task 1: The logo SVGs

**Files:**
- Create: `site/assets/cardak-mark-light.svg`, `site/assets/cardak-mark-dark.svg`, `site/assets/cardak-mark-mono.svg`, `site/assets/cardak-wordmark-light.svg`, `site/assets/cardak-wordmark-dark.svg`, `site/assets/cardak-icon.svg`
- Test: `tests/test_brand.py`

**Interfaces:**
- Produces: the six files above. `cardak-icon.svg` is the dark tile by default and switches to the light tile with an internal `<style>` `@media (prefers-color-scheme: light)` rule. `mark-light` = Danube night C + paprika háček; `mark-dark` = linen C + paprika háček; `mark-mono` = graphite C and háček. All transparent outside the tile.

- [ ] **Step 1: Write the failing tests** in `tests/test_brand.py`, with `ASSETS = ROOT / "site" / "assets"` and `PALETTE = {"#0f2a44", "#c8352b", "#f6efe2", "#17181c"}`:

```python
SVGS = ["cardak-mark-light.svg", "cardak-mark-dark.svg", "cardak-mark-mono.svg",
        "cardak-wordmark-light.svg", "cardak-wordmark-dark.svg", "cardak-icon.svg"]

@pytest.mark.parametrize("name", SVGS)
def test_svg_parses_and_has_a_view_box(name): ...   # ET.parse ok; root tag is {svg ns}svg; viewBox is "0 0 100 100" (wordmarks "0 0 460 100")

@pytest.mark.parametrize("name", SVGS)
def test_svg_uses_only_the_palette(name): ...       # every #rrggbb in the text (case-insensitive) is in PALETTE; "#fff"/"#000" only inside <mask>

@pytest.mark.parametrize("name", SVGS)
def test_svg_has_no_editor_metadata(name): ...      # no "inkscape", "sodipodi", "<metadata", "data:" in the text

@pytest.mark.parametrize("name", SVGS)
def test_the_cut_is_a_mask(name): ...               # a <mask> exists whose path d is "M38 14 L50 33 L62 14" with stroke-width 16

def test_mono_mark_is_one_ink(): ...                # the only palette color in cardak-mark-mono.svg is #17181c

def test_wordmark_letters_follow_the_spec(): ...    # both wordmarks contain the r, d and k paths verbatim from Global Constraints, and exactly one háček path
```

- [ ] **Step 2: Run** `uv run pytest tests/test_brand.py -v` — Expected: FAIL (files missing).
- [ ] **Step 3: Write the six SVGs** from Global Constraints: plain `<svg xmlns=… viewBox=…>`, a `<mask>` (white rect, black háček path at stroke 16) applied to the C (and in the wordmark to the C only), then the háček at stroke 7. Icon: a `rx="22"` rounded rect 0 0 100 100 under the mark, classes for the three fills so the media query swaps them. Keep each file under ~40 lines and readable.
- [ ] **Step 4: Run** `uv run pytest tests/test_brand.py -v` — Expected: PASS. Then open each SVG in Chrome on a white and a black background and compare with the approved mockup (the háček clearly separate from the C at 16 px in the icon).
- [ ] **Step 5: Commit** `feat(site): the cardak logo as SVG (#66)`.

### Task 2: The PNGs

**Files:**
- Create: `scripts/make_brand_pngs.py`, `site/assets/cardak-icon-32.png`, `site/assets/cardak-icon-180.png`, `site/assets/cardak-mark-mono-512.png`
- Test: `tests/test_brand.py` (extend)

**Interfaces:**
- Consumes: `cardak-icon.svg` (rendered in its default, dark state) and `cardak-mark-mono.svg` from Task 1.
- Produces: `main() -> None` in the script; PNG files at the sizes in their names. The 32 and 180 px icons are opaque (the tile); the 512 px mono mark has a transparent background.

- [ ] **Step 1: Write the failing tests**:

```python
@pytest.mark.parametrize(("name", "size"), [("cardak-icon-32.png", 32), ("cardak-icon-180.png", 180), ("cardak-mark-mono-512.png", 512)])
def test_png_has_its_size(name, size): ...          # bytes start with b"\x89PNG"; IHDR width and height (struct.unpack(">II", data[16:24])) == (size, size)

def test_mono_png_is_transparent(): ...             # IHDR color type (data[25]) is 6 (RGBA)
```

- [ ] **Step 2: Run** `uv run pytest tests/test_brand.py -v` — Expected: the new tests FAIL.
- [ ] **Step 3: Write `scripts/make_brand_pngs.py`** (PEP 723 header with no dependencies, like `make_sounds.py`'s style): for each (svg, png, size) it writes a temporary HTML page that shows the SVG at `size`×`size` with no margin, and runs `google-chrome --headless=new --disable-gpu --hide-scrollbars --default-background-color=00000000 --window-size=S,S --screenshot=<png> file://<html>`. A missing `google-chrome` exits with a one-line message. Run it once: `uv run scripts/make_brand_pngs.py`.
- [ ] **Step 4: Run** `uv run pytest tests/test_brand.py -v` — Expected: PASS. Look at the three PNGs (Read tool) — the 32 px tile still shows a separate háček.
- [ ] **Step 5: Commit** `feat(site): PNG icons rendered from the logo (#66)`.

### Task 3: The landing page

**Files:**
- Create: `site/index.html`
- Test: `tests/test_site.py`

**Interfaces:**
- Consumes: the asset file names from Tasks 1–2.
- Produces: `site/index.html` containing `__VERSION__` exactly once (the footer) and no other `__NAME__` placeholder.

- [ ] **Step 1: Write the failing tests** in `tests/test_site.py` (parse with `html.parser.HTMLParser` collecting tags and attributes, plus plain-text checks):

```python
def test_every_asset_it_references_exists(): ...    # each src/href/srcset starting "assets/" names a file in site/assets
def test_it_requests_nothing_from_elsewhere(): ...   # no src/srcset/<link href> starting http(s) or //; no <script src>; <a href> to https://github.com/brlauuu/cardak… allowed
def test_paths_are_relative(): ...                   # no src/href starting "/"
def test_wordmark_follows_the_color_scheme(): ...    # a <picture> with <source media="(prefers-color-scheme: dark)" srcset="assets/cardak-wordmark-dark.svg"> and an <img src="assets/cardak-wordmark-light.svg" alt="cardak">
def test_favicons_are_linked(): ...                  # <link rel="icon" type="image/svg+xml" href="assets/cardak-icon.svg">, rel="icon" 32 px png, rel="apple-touch-icon" 180 px png
def test_version_placeholder_appears_once(): ...     # text.count("__VERSION__") == 1 and re.findall(r"__[A-Z_]{2,}__", text) == ["__VERSION__"]
def test_video_placeholder_says_how_to_swap(): ...   # an element with id="demo" exists, and a comment contains "<video" and "assets/cardak-demo.mp4"
def test_it_tells_how_to_install(): ...              # "uv tool install git+https://github.com/brlauuu/cardak" in text
def test_sections_in_order(): ...                    # positions: the <picture> < id="what" < id="features" < id="demo" < id="install" < <footer>
def test_has_a_description_and_social_tags(): ...    # <meta name="description">, og:title, og:description, og:image (absolute https://brlauuu.dev/cardak/assets/cardak-icon-180.png is allowed for og:image only)
```

- [ ] **Step 2: Run** `uv run pytest tests/test_site.py -v` — Expected: FAIL.
- [ ] **Step 3: Write `site/index.html`.** Sections in the order the test pins: the wordmark (`<picture>`, large), `#what` (one or two sentences from the README's opening), `#features` (a list of 6–9 short items drawn from the spec's list), `#demo` (a 16:9 frame in Danube night with a linen "The video is on its way" line, and next to it the comment `<!-- To add the video: put it at assets/cardak-demo.mp4 and replace #demo's contents with <video src="assets/cardak-demo.mp4" controls muted playsinline preload="metadata"></video> -->`), `#install` (requirements: Linux, WezTerm, Claude Code for the agent features; the `uv tool install` line and `cardak`; a link to the README's Install section), `<footer>` (GitHub link, `v__VERSION__`). One inline `<style>`: CSS custom properties for the palette, a `@media (prefers-color-scheme: dark)` block (Danube night page, linen text, paprika accents), system font stack, max-width column, works at 390 px. Feature copy is plain "cardak" text; made-up project names only.
- [ ] **Step 4: Run** `uv run pytest tests/test_site.py -v` — Expected: PASS. Screenshot `file://…/site/index.html` with headless Chrome at 390×844 and 1180×820, once with `--blink-settings=preferredColorScheme=0` (dark) and once with `preferredColorScheme=1` (light), and read the four images: no horizontal scroll, wordmark swaps, text readable.
- [ ] **Step 5: Commit** `feat(site): the landing page (#66)`.

### Task 4: Build and deploy

**Files:**
- Create: `scripts/build_site.py`, `.github/workflows/pages.yml`
- Test: `tests/test_build_site.py`

**Interfaces:**
- Consumes: `site/` from Tasks 1–3.
- Produces: `build(src: Path, out: Path, pyproject: Path) -> str` (returns the version it wrote; replaces `out` entirely) and `main() -> int` in `scripts/build_site.py`; `python scripts/build_site.py` builds `site/` → `_site/` from the repo root and exits non-zero on a leftover placeholder. Tests import it with `importlib.util.spec_from_file_location`.

- [ ] **Step 1: Write the failing tests**:

```python
def test_build_fills_the_version(tmp_path): ...       # src with index.html "v__VERSION__" + assets/x.svg, pyproject version "1.2.3" → out/index.html contains "v1.2.3", out/assets/x.svg copied, returns "1.2.3"
def test_build_replaces_an_old_output(tmp_path): ...  # a stale out/old.txt is gone afterwards
def test_leftover_placeholder_fails(tmp_path): ...    # index.html with "__STARS__" → raises ValueError naming "__STARS__"
def test_real_site_builds(tmp_path): ...              # build(ROOT/"site", tmp_path/"o", ROOT/"pyproject.toml") == tomllib version, and "__" placeholders gone
def test_workflow_triggers_and_permissions(): ...      # pages.yml text: branches [main], paths include "site/**", "pyproject.toml", ".github/workflows/pages.yml", "scripts/build_site.py"; workflow_dispatch; no "tags:"; permissions contents read, pages write, id-token write; runs "scripts/build_site.py"; uses upload-pages-artifact with path _site
def test_site_is_not_packaged(): ...                   # in tests/test_packaging.py: pyproject.toml has no "source-include" or "wheel-include" naming "site" (a text check, like the packs test)
```

- [ ] **Step 2: Run** `uv run pytest tests/test_build_site.py tests/test_packaging.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement `scripts/build_site.py`** (stdlib only; `rmtree` then `copytree`; version from `tomllib` `project.version`; placeholder regex `__[A-Z_]{2,}__` checked on every `.html` in `out`) and **`.github/workflows/pages.yml`** (checkout, `actions/setup-python@v5` with 3.13 or `astral-sh/setup-uv@v6` + `uv run --no-project`, run the script, configure-pages, upload `_site`, deploy; `concurrency: group pages, cancel-in-progress: true`; environment `github-pages`). Add `_site/` to `.gitignore`.
- [ ] **Step 4: Run** the tests — Expected: PASS. Then `uv run ruff check && uv run ruff format --check && uv run pyright && uv run pytest --cov=cardak --cov-branch --cov-fail-under=80` — Expected: all green.
- [ ] **Step 5: Commit** `ci: publish the landing page with GitHub Pages (#66)`.

### Task 5: README, AGENTS.md, CHANGELOG

**Files:**
- Modify: `README.md` (top), `AGENTS.md` (Layout, and a "Logo and site" section), `CHANGELOG.md` (Unreleased → Added)
- Test: `tests/test_site.py` (extend)

- [ ] **Step 1: Write the failing test**: `test_readme_shows_the_wordmark` — README starts with a `<picture>` whose sources are `site/assets/cardak-wordmark-dark.svg` (dark) and `site/assets/cardak-wordmark-light.svg`, with `alt="cardak"`, linked to `https://brlauuu.dev/cardak/`, and the `# cardak` heading kept for text readers.
- [ ] **Step 2: Run** it — Expected: FAIL.
- [ ] **Step 3: Edit.** README: the `<picture>` above the heading. AGENTS.md: `site/` (`index.html`, `assets/`), `scripts/{build_site, make_brand_pngs}.py` and `.github/workflows/pages.yml` in Layout; a short section: the palette with hex values, the stylized Č only in images, how to regenerate the PNGs, how to add the video, that the page is served at brlauuu.dev/cardak through a proxy and needs the trailing slash. CHANGELOG: "cardak has a logo and a page at brlauuu.dev/cardak (#66)".
- [ ] **Step 4: Run** the full CI set from Task 4 Step 4 — Expected: green. Scan the branch's changed files for home paths and for names of the author's other projects — Expected: nothing.
- [ ] **Step 5: Commit** `docs: the logo in the README, the site in AGENTS.md (#66)`.

### Task 6: Publish (live, with the user's settings changes)

No new code; the steps that touch GitHub settings and the second repository.

- [ ] **Step 1:** Fresh opus final review of the branch; fix Critical/Important with a failing test first. Push, open the PR (`Closes #66`). Merge only when the user says so.
- [ ] **Step 2: Enable Pages** (approved): `gh api -X POST repos/brlauuu/cardak/pages -f build_type=workflow`. Expected: JSON with `"build_type": "workflow"`.
- [ ] **Step 3:** After the merge, watch the Pages run (`gh run watch`), then `curl -sI https://brlauuu.github.io/cardak/` → 200 and `…/assets/cardak-icon.svg` → 200 `image/svg+xml`.
- [ ] **Step 4: The site repository** (brlauuu.github.io, its own AGENTS.md conventions, a branch and a PR there): in `vercel.json` add `{ "source": "/cardak", "destination": "/cardak/", "permanent": true }` to `redirects` and `{ "source": "/cardak/(.*)", "destination": "https://brlauuu.github.io/cardak/$1" }` to `rewrites`; in `_data/projects.yml` add a `Cardak` entry (`repository: brlauuu/cardak`, `logo_url: https://raw.githubusercontent.com/brlauuu/cardak/main/site/assets/cardak-mark-mono-512.png`, `website_fallback:` empty, a `counter` block like the others with `fallback: "—"`). Run that repo's own checks. Merge when the user says so.
- [ ] **Step 5: Set the About website** (approved): `gh repo edit brlauuu/cardak --homepage https://brlauuu.dev/cardak/`.
- [ ] **Step 6: Verify live:** `curl -sI https://brlauuu.dev/cardak` → 308 to `/cardak/`; `/cardak/` → 200; headless Chrome at 390×844 and 1180×820, light and dark, on `https://brlauuu.dev/cardak/` (images load through the proxy); the brlauuu.dev project list shows the mono mark in both themes and its website icon opens the page.
