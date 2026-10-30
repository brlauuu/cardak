# Context usage over time (#86) — design

## Intent

The user wants to see, across sessions and over days, how their sessions' context grows: how often
a session climbs until Claude Code auto-compacts it, and how often it's cleared or left early. The
point is to decide whether to clear context more often. They explicitly don't want compactions or
clears recorded as events; the shape of the data should show them:

- a **compaction** is a sharp drop inside one session;
- a **clear** (`/clear`, a new session id from the same pane) or moving on is a session's line
  ending.

Success: in the deck and on the phone, the user can look at a day (and, on the phone, the whole
month) and see each session's context as a line on a log scale, sawtooth where it compacted, and
read off how high sessions get before that happens.

## What the data looks like (measured 2026-10-29, read-only, the user's last 30 days)

- Context of a reply = `input_tokens + cache_read_tokens + cache_write_5m_tokens +
  cache_write_1h_tokens` (the request's whole input), as already stored in `claude_replies`.
- **Subagent replies share their parent's `session_id`.** They live in files below the session
  (`<project dir>/<session id>/subagents/agent-*.jsonl`, each line with `isSidechain: true` and an
  `agentId`), and their context is small (tens of k), so a per-session line zigzags between the
  main thread and its subagents several times a minute. `claude_replies` doesn't record which
  replies are subagents' today.
- On main-thread replies only, "the context fell below half of the previous reply's, and that one
  was over 100k" matched Claude Code's own `compact_boundary` records 29 of 30 times; the other
  five hits were replies reporting 0 tokens (error/synthetic replies), which must be ignored.
  With those ignored there were no false drops.
- All 30 compactions were automatic, at 205k–597k (mostly 250–400k), not at one fixed ceiling;
  sessions run with the 1M window (peaks up to 713k). So no "max" line is drawn: the peaks before
  each drop show where it fired.
- Session peaks: 22 of 60 above 300k, 6 above 600k, ~20 never above 100k.

## Goals

1. **Main thread only.** Subagent replies are left out of everything this feature shows. cardak
   records, per stored reply, whether it came from a subagent (`isSidechain` on the transcript
   line). Replies already stored when the new version first runs get the flag by reading the
   transcripts of the history window (`trends.DAYS`, files modified in the last 30 days) again
   once, in the background. The backfill only flags replies already in `claude_replies`; it stores
   no new ones (my call: spend and trends stay exactly as they were, and the context series covers
   the same days the trends do).
2. **Context series** (core, pure): per main-thread session, its replies' (ts, context) in order,
   0-token replies dropped, over the history window, per project as `group_trends` folds cwds
   (a session is under the project of its cwd). Derived per project and day, for the table and
   notes: peak context, number of sessions with a main-thread reply that day, number of **drops**
   (the rule above) that day, and how many sessions **ended without a drop** that day (cleared or
   left before auto-compact; my call, replacing an arbitrary "under 100k"). These are computed from
   the replies when read, never stored as events.
   - **Ended** (my call): a session ends on the local day of its last main-thread reply, and only
     once that reply is an hour old (so a session still in use isn't counted). It counts as
     "without a drop" if it had none over its whole life, else it's just ended. A session crossing
     midnight is in both days' session counts but ends on one.
   - **Resumed sessions** (my call): a reply copied into a resumed session's file is stored once,
     under whichever session it was first read from, at its original timestamp, as today. Its point
     lands at the right time either way; the line may change color at the resume. Not worth a
     schema change.
   - **Freshness** (my call): the series is rebuilt in the counter thread like the trends, so the
     agent-row figure (goal 3) can lag a working agent by up to the counter's 30 s pass.
3. **Deck, agent rows (my call):** each Claude agent row shows its session's current context (the
   latest main-thread reply's), e.g. `312k`, beside the existing status/usage text. Nothing for a
   session with no reply yet.
4. **Deck, history screen:** `c` gains a **context** figure after the existing ones. Each day cell
   is that day's peak context for the project (Other, All projects as today). Bands for context
   are on a log scale (my call: so a 50k day and a 600k day land in different bands). Selecting a
   cell notifies a line like `Oct 28: 5 sessions · peak 597k · 4 drops · 2 ended without one`.
5. **Deck, plot:** Enter on a context cell opens a plot of that project's sessions **for that
   day** (my call: over 30 days a braille column would be ~3 h and the sawtooth disappears):
   - x: the day, midnight to midnight (my call; a session crossing midnight shows its part);
   - y: **log scale**, 10k at the bottom to 1M at the top, gridlines and labels at 10k, 100k, 1M
     (values below 10k sit on the bottom row, above 1M on the top);
   - one line per session, each in its own color from a small palette (cycling);
   - ←/→ change the day within the window, as the RAM plot does; a line under the plot lists the
     day's sessions (peak, drops, final context).
6. **Phone, Trends sheet:** the figure picker gains **context**:
   - one bar per day per project at the day's peak, bar height on the same log scale;
   - a **month plot** (the user asked for month-long data on the phone): all of the project's
     sessions over the 30 days, one line per session, log y axis with 10k/100k/1M gridlines, date
     labels on x, as inline SVG;
   - tapping a day (a bar, or the month plot) shows that day's plot, the same as the deck's, with
     the day's line of text.
   Data comes from the Bearer-protected API like `/api/trends` (a new endpoint or a field; the
   builder's call), whitelisted in `web/view.py` like the rest: numbers, names and kinds only, no
   session ids beyond an opaque per-response index, no cwd paths.

## Non-goals

- Recording compactions or clears as events, hooking `PreCompact`, or reading `compact_boundary`
  lines for the feature (it was only used to check the rule above).
- A percentage of the context window, or a "max" line: the window size isn't reliably known per
  session and auto-compact doesn't fire at a fixed level.
- Advice ("you should clear now"), alerts or notifications about context.
- Subagents' own context.

## Anti-goals

- **Spend and trends changing.** Adding the flag and re-reading transcripts must not double or
  drop a reply in the month's spend or the usage trends; the existing figures (which include
  subagents, correctly, since they cost money) stay as they are.
- **Breaking older readers.** The store's rules in AGENTS.md hold: only a new table is safe for
  older code; a new column on `claude_replies` means raising `MIN_READER`. (Builder picks; a new
  table keyed by `reply_key` would keep older cardak readers working.)
- **A slower hook.** None of this runs in `cardak hook`; its import guards still hold.
- **Leaking on the web.** No paths, prompts or session ids leave through the API.
- **A janky deck.** The series is built in the counter thread with the trends (reload only when
  `usage_mark` or the date changes, as #88 does), not on the UI thread per redraw.

## Constraints

- Layering and one-interface-per-format (AGENTS.md): transcript parsing stays in
  `collectors/usage.py`; the series and the derived numbers in `core` (pure, e.g. a new
  `core/context.py`); drawing in `ui/` and `web/page.html`. `ui/plot.braille` is linear today; a
  log-scale variant or a transform before it is the builder's choice.
- Tests: synthetic transcripts in `tests/fixtures/` (main thread plus a `subagents/` file, a
  compaction-shaped drop, a 0-token reply), never the real `~/.claude`; the drop rule, log scale
  mapping, per-day numbers and the web whitelist each tested.
- The page's CSP: no inline handlers or `style=` attributes; SVG built with DOM calls, sized via
  attributes or CSSOM as the bars are today.
- Docs: AGENTS.md (the subagent finding, the drop rule, the new table/flag and its backfill, the
  endpoint), README where the history screen and Trends sheet are described, CHANGELOG
  Unreleased → Added, one line (#86).

## Left to the builder

- Table vs column for the flag, and how the one-time backfill is triggered and recorded.
- The exact log band edges for the history table, the palette, the plot height.
- How many points per session go to the phone (downsampling that keeps each drop's before/after).
- Whether the deck's agent-row figure goes on the row's text or the detail (`i`) when space is
  tight.
