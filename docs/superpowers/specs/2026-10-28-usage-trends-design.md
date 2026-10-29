# Usage trends per project (#88) — design

## Intent

#29 started storing one row per Claude Code reply (`claude_replies`: session, cwd, model,
token counts) and one per prompt the user typed (`claude_prompts`: session, cwd, length in
characters) in `cardak.db`. #29 itself only shows this month's cost. The user wants to see
**trends**, out of curiosity: how their use of Claude Code changes from day to day, per project
and overall. Examples: prompts getting shorter, sessions getting longer, one week being unusually
heavy, a project going quiet. There's no decision to support and no alerting. Success means
opening a screen and reading a trend at a glance.

It must be visible **in the deck and on the phone/iPad web page**. In the deck, the user is
happy for it to live in the existing history screen (`h`).

## Goals

- Day-by-day usage per project for the last **30 days** (the same window as the RAM/CPU history,
  so one screen doesn't mix window lengths), plus an "Other" row for folders in no project and
  an **"All projects" row** for the whole machine (the user asked to see "all projects together"
  in the playback; for #29 they asked for "per-project and in total").
- The figures, one at a time (`c` switches, as it does between RAM and CPU today):
  1. **cost**: USD at API prices, priced with the same rules and price book as #29;
  2. **tokens**: everything that went through (input + output + cache read + cache writes);
  3. **prompts**: prompts typed;
  4. **sessions**: distinct sessions with at least one reply that day (a session that runs past
     midnight counts on both days);
  5. **prompt length**: average characters per prompt;
  6. **reply length**: average output tokens per reply;
  7. **per session**: average tokens per session.
- The same figures on the web page, in a new "Trends" view.

## Non-goals

- RAM/CPU history on the web page. That's a separate request; it may come later.
- More than 30 days, though replies are kept for 62.
- Data from before #29 shipped. The user already accepted that it only counts from then on.
- Per-model breakdowns, per-session drill-down, export, alerts.
- The hour-by-hour graph that a RAM/CPU cell opens. A usage cell does not open it (see below).

## Anti-goals

- A slower deck or service. The counter's measured budget (about 70 ms a pass, 0.3% of a core,
  50 MB peak in the daemon) must hold. No per-poll scan of a month of rows, on either the deck
  or the web side.
- Two different numbers for the same thing. A project's cost for the month must add up the same
  way here as in #29's figure. Days come from the local calendar, like the RAM history and
  `month_start`.
- Anything leaving the machine that the web view's whitelist doesn't allow: no cwd paths, no
  session ids, no prompt text (none is stored anyway).

## The deck: the history screen (`h`)

- `c` cycles through RAM → CPU → the seven usage figures → back to RAM. The title names the
  current figure and the next one, like `[c] next: tokens` (my call: one key, cycling, rather
  than a second key for usage).
- Same table: one row per project, 30 one-character day cells, shaded in five bands. RAM/CPU
  bands come from the pane thresholds. Usage figures have no threshold, so **their bands are
  relative to the busiest cell in the table for that figure** (¼, ½, ¾, all of it), and the
  legend says so with the real values. The All projects row is left out of that scale (it would
  always be the busiest and flatten every project) and is shaded against its own busiest day. My call; quantiles would also do if the builder finds
  one outlier flattens everything else.
- For usage figures the fixed columns become `today`, `per day` (the mean over days that had any
  use) and `peak`. The fourth column, `alerts` for RAM/CPU, becomes `30 d`, the window's total.
  For the averaged figures (prompt length, reply length, per session) it's the 30-day average,
  weighted by prompts, replies or sessions rather than averaged across days. Over the window a
  session counts **once** (my call): `30 d` for sessions is the distinct sessions in the window,
  and per session's 30-day average is the window's tokens over those sessions. So "sessions
  getting longer" reads from the day cells, where each day divides that day's tokens by that
  day's sessions.
- Cost cells and totals with unpriced replies (fast mode, a model with no price) carry the same
  `+` as #29's figure (my call), and the legend names the models, like #29's warning.
- Rows: every project with usage in the window, sorted by `s` as today. A project with usage but
  no RAM history still appears, and rows appear or disappear as the user switches between
  RAM/CPU and usage. Other comes after the projects and All projects last, whatever the sort.
- Selecting a usage cell shows that project-day's numbers in a notification or small panel: all
  seven figures for that day. It does not open the plot screen.
- A day with no use is a blank cell. Averages on a day with nothing to average are blank, not 0.
- No figures yet (a fresh install, or the counter hasn't finished its first pass): the usage
  views say so in the hint line, like the RAM history's `EMPTY`.

## The web page

- A "Trends" button opens a view (the existing `#sheet` or its own section; the builder's call)
  with the same seven figures and a control to switch between them.
- One row per project: its name, then a strip of 30 small bars, one per day, scaled to the
  busiest day in the view (All projects scaled on its own, as in the deck), then `today` /
  `per day` / `peak` as text. Other, then All projects, come last. A `+` marks incomplete cost. It must work
  at 390 px wide (phone) and look fine at iPad width.
- The page's CSP has no inline styles, so bars can't use `style=` attributes. Use CSSOM
  (`el.style.height = …`, which CSP allows), SVG built with `createElementNS`, or a canvas.
  Text still goes in through `textContent` only.
- Data comes from a new authenticated endpoint, `GET /api/trends` (Bearer key, like the others),
  fetched when the view opens and refreshed at most every 30 s while it's open. It isn't fetched
  on every snapshot poll. It returns project names and per-day numbers only.

## Where the numbers come from

- Both the deck and `cardak daemon` already run a `SpendCounter` thread (`core/spend.py`). After
  storing new rows each pass (every 30 s), the counter also builds the trends: per (cwd, local
  day), the sums needed for every figure (cost, the token kinds, prompts and their characters,
  replies and their output tokens) plus each day's set of session ids, so that sessions are
  counted once even when one session's replies span several cwds of one project. It does this in
  SQL with `GROUP BY` over the window rather than loading every reply into Python, and keeps the
  result next to `latest`. Cost needs the price per model, so the grouping includes the model and
  `speed`, and whether the reply's whole input (fresh + cache read + cache written) is over the
  model's long-context threshold, since #29's `reply_cost` prices that tier per reply. Without it
  the two cost figures wouldn't match. Fast-mode or unpriced replies count toward tokens but not
  cost, as in #29, and the counts of unpriced replies are kept per (cwd, day) for the `+`. Session
  ids are kept per day and over the window, for the whole machine as well as per project, so that
  All projects and the window figures count each session once.
- Mapping cwd → project happens in `LiveSource`, as `_spend_by_group` does, and is cached the
  same way: resolved again only when the trends or the config change.
- The deck's history screen gets the mapped trends from the source; the daemon's web server gets
  them for `/api/trends`. `web/view.py` whitelists the output, as it does for snapshots.
- Pure aggregation (filling 30 days, means, peaks, weighted averages, band edges) lives in core
  next to `core/history.py`, without Textual, so the deck and the web share it.
- A trends figure built in a pass more than a pass or two old, or for a window that no longer
  ends today, isn't shown (the same rule as #29's last-month guard).

## Constraints

- Layering, the one-interface-per-format rule, the CSP rules, test isolation and the `cardak
  hook` budget, all as in `AGENTS.md`. The hook must import none of this.
- No new tables: the trends are computed from the existing ones. If the builder finds an index is
  needed for the `GROUP BY` (on `claude_replies.ts`, say), adding an index is fine for older
  readers.
- Tests use recorded/synthetic rows, never the real `cardak.db` or transcripts.
- Docs: AGENTS.md (history screen, web endpoints), CHANGELOG Unreleased → Added, one line (#88).

## Left to the builder

- Exact column widths, labels and number formats (reuse `fmt_usd`; tokens as `1.2M`, `340k`).
- Whether the web view reuses `#sheet` or gets its own section.
- The SQL shape and whether the trends are rebuilt every pass or only when rows were added.
