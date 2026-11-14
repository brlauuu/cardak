# #164: the landing page, polished, with a slide per feature

## Intent

#106 gave the landing page a tagline, the video beside the install and seven slides. Its owner
wants it closer to the style of their other project pages (a release banner on top, "Created by
brlauuu" and the changelog at the bottom), the header centered, the video wider with the install
below it in one tabbed card, and every feature of the deck shown, not seven.

## Goals

1. **Header centered**: the wordmark and the tagline centered on the page. The tagline is on one
   line from tablet width up (no wrap), and wraps to at most two lines on a phone.
2. **Video wide**: the video slot takes the page's full content width; the install goes below it.
3. **Install card**: one card. The repo link and the needs line at its top, then two tabs,
   **Full** (selected on load) and **Basic** (the old "Lite"), each with its one-line description,
   its command and a copy button; the two setup steps and the README link below the tabs, for
   both. Real ARIA tabs (`role="tablist"`, `tab`, `tabpanel`, `aria-selected`, ←/→ between tabs).
   Without JS, both panels show, one after the other, and the tab buttons are hidden.
4. **Footer**, as on the owner's other pages: "Created by [brlauuu](https://brlauuu.dev)", and
   below it "v__VERSION__ · [changelog](CHANGELOG.md on GitHub)", centered, muted.
5. **Release banner** at the top: "cardak **X.Y.Z** has been released →", linking to the GitHub
   release `vX.Y.Z`. It starts `hidden`; the script shows it when the release is under 30 days
   old by the visitor's clock. The date comes from CHANGELOG.md's `## X.Y.Z — YYYY-MM-DD` heading
   for the version in `pyproject.toml`, filled in by `scripts/build_site.py` as `__RELEASED__`
   (empty when the version has no dated heading: no banner). The changelog, not the tag, because
   the release PR's merge deploys the page before the tag exists. (My call.)
6. **A slide per feature**, captions centered. The seven stay; the new ones, each with a screen
   from `scripts/make_screens.py` on the same invented data:
   - **The summary model**: the Summaries screen (`s`): providers, the model, the month's cost.
   - **Context, session by session**: the context plot (one project's sessions over a day, with
     a compaction).
   - **Alerts before memory runs out**: the event log (`e`) with memory, growth and crash lines.
   - **Checkpoint, then recap**: the recap screen (`r`): the latest handoff note and the commits
     since.
   - **Clean up a repo**: the `x` confirmation over the deck.
   - **Find anything**: `/` with a query, the matching panes.
   - **Fold what you don't need**: projects folded with ←.
   - **Add a project**: the `a` dialog on a pane in Other.
   - **Docker counts too**: the deck with a project's containers and a running build.
   - **Tab titles** and **notification sounds** have no screen in the deck: each gets an
     illustration made by the script, in the deck's look (a tab bar named by project; the
     `[notify]` settings with the sound themes). (My call.)

   Order: overview, waiting, summaries, summary model, find, fold, add, Docker, usage, context,
   alerts, board, recap, cleanup, colors, tab titles, sounds, phone (18).

## Constraints

- Everything in #106's spec still holds: plain HTML/CSS, one small inline script, nothing loaded
  from elsewhere, relative paths, brand colors, light and dark, no horizontal scroll at 360 px,
  44 px tap targets, alt text and sizes on every image, lazy after the first slide.
- The script may grow past #106's 4000-character test limit for the tabs and the banner; keep it
  under 6000.
- Images from invented data only; the machine-leak test covers every new slide.

## Non-goals

- The video itself (#105). Star and fork counts (podlog has them; not asked).
