# What this month would cost at API prices — design

Issue #29: "I want to see the total spend over the month in the cardak screen."

## Why

The user pays a flat Claude Code subscription. They want to see what their Claude Code usage
would have cost this month if they paid per token at Anthropic's API prices: a "what am I getting
out of the plan" number, overall and per project, on the deck and on the phone.

PLAN.md §3 lists "cost dashboards" as a non-goal. This is not a dashboard: one figure in the
header and one per project row, beside the RAM and CPU numbers that are already there. Update the
non-goal to say so ("a monthly figure, not a cost dashboard").

## Goals

1. **The deck header** gains one more part, after the existing ones:

   ```
   cardak · 4 projects · 9 panes · 2 waiting · $412 this month at API prices
   ```

   It covers **all** Claude Code usage on this machine this month, not only the sessions open in
   the deck: closed sessions, projects with no pane open, sessions that belong to no project,
   subagents, `claude -p` runs.
2. **Each project row** in the deck (Other's row too) shows that project's figure for the month,
   next to its RAM and CPU:

   ```
   ▾ webshop   $37 this month   1.2 GB  4%
   ▾ api-gw    $112 this month  800 MB  2%
   ```

   A project with no spend this month shows nothing there rather than "$0".
3. **The phone web view** (M7) shows the same: the total near the machine numbers at the top,
   and each project card's figure.
4. **"This month"** is the local calendar month, from midnight on the 1st until now
   (`core/summary.month_start`). On the 1st it starts again from zero.

## Non-goals

- **History before the feature exists.** The user said it is fine if counting starts once the
  feature ships. If the first pass can cheaply take in what this month's transcripts still hold,
  it should (builder's call, see "First pass").
- A breakdown by model, by day, by session, or past months. No graphs, no history view.
  (Per-project usage stats are #88; this issue only stores their data, see below.)
- Spend for projects that have no pane open right now on their own line. Their spend is in the
  header total only; the user accepted this.
- Usage outside Claude Code on this machine: claude.ai chats, other machines, cardak's own
  waiting summaries (those already show in `i`, and they are not Claude Code usage).
- Anything that limits, warns about or caps the spend.

## Anti-goals (a failure even if it works)

- **Slowing the deck or the daemon.** `~/.claude/projects` holds ~480 MB of transcripts on the
  dev machine. Re-reading them each poll is out. A pass reads only what was appended since the
  last one, off the UI thread, and the daemon stays near its M5 budget (under 1% of one core).
- **Touching `cardak hook`.** It stays under its 150 ms budget and imports nothing new: the
  counting is done by the deck and the daemon, never in the hook.
- **Counting a reply twice.** Claude Code writes one transcript line per content block, each
  repeating the same `message.id` and `usage`, and a resumed or forked session copies earlier
  lines into a new file. Each API reply is counted once (key: `message.id` plus `requestId`, the
  way ccusage does it). The deck and the daemon may both be counting at the same time; they
  must not double the figure between them.
- **Presenting a guess as exact.** The header says "at API prices", and a model cardak has no
  price for is not silently priced as zero (see Pricing).

## How it works

### Reading

- Claude Code's transcripts live in `~/.claude/projects/<dir>/<session>.jsonl`, with subagent
  transcripts in files below them. Find every `*.jsonl` file under `~/.claude/projects`, at any
  depth.
- Lines that matter are `type: "assistant"` records with `message.usage`. Fields used (all seen
  in `tests/fixtures/transcripts/basic.jsonl`): `timestamp`, `cwd`, `requestId`, `message.id`,
  `message.model`, and in `message.usage`: `input_tokens`, `output_tokens`,
  `cache_read_input_tokens`, `cache_creation_input_tokens`, and
  `cache_creation.ephemeral_5m_input_tokens` / `ephemeral_1h_input_tokens` (the two cache write
  rates differ). `isSidechain` lines count: they are real usage. Synthetic messages (model
  `<synthetic>`, all-zero usage) are skipped.
- **Per AGENTS.md, all transcript parsing lives in `collectors/transcripts.py`** (or a sibling
  collector module); nothing else reads under `~/.claude/`. The format is not a stable API:
  anything unexpected is skipped, never raised. Record a sanitized fixture for each case the
  parser handles (subagent file, resumed session, duplicate content blocks, synthetic message).
- **Incremental:** remember per file how far it was read (path, size or inode, byte offset) in
  the database. A pass looks at files whose size changed, reads from the offset to the last
  complete line, and starts over from 0 for a file that got shorter or was replaced. A file
  modified before this month's start is not read.
- Passes run in the daemon (every poll or less often, builder's call; it serves the web view
  anyway) and in the deck (in a worker, so a user without the daemon still gets the number).

### Storing

- A **new table** in `cardak.db`, one row per API reply: the dedupe key as primary key, the time,
  the `cwd`, the model and the token counts. Insert-or-ignore makes the deck and daemon safe to
  run side by side. Commit in small batches: hook processes share the database with a 2 s busy
  timeout, and a first pass holding the write lock for long would push `cardak hook` past its
  150 ms budget. Token counts are stored, not dollars, so a corrected price changes the
  figure without re-reading anything.
- A new table is safe for older readers: bump `SCHEMA_VERSION`, keep `MIN_READER`. Rows older
  than 62 days are pruned (like `summaries`). Add the table to the AGENTS.md files table's
  `cardak.db` row.
- Rows kept in cardak's database survive Claude Code deleting old transcripts (30 days by
  default), so a full month always adds up.

### Usage stats, stored now, shown later (#88)

The user asked to store, while the transcripts are being read anyway, what a later view needs
for per-project stats: how long their messages are, token use, averages per message and per
session. Showing them is #88; this issue only stores them, because adding a column to a
shipped table would raise `MIN_READER`, and a new table now costs nothing.

- Each reply row also keeps its `sessionId`.
- One row per prompt the user typed: its `uuid` (key), time, `sessionId`, `cwd` and length in
  characters (text blocks only). Not prompts: `isMeta` lines, `isSidechain` lines (Claude
  prompting a subagent), and user lines that only carry tool results.
- Same pruning (62 days) and the same incremental pass and offsets as the replies.

### Pricing

- **Prices are downloaded, so the user never has to know when to update them.** The user asked
  for it: once a day, **only while cardak runs** (the deck or `cardak daemon`), cardak downloads
  LiteLLM's public price list (`model_prices_and_context_window.json` on GitHub raw), keeps the
  Claude entries, and caches them in `~/.cache/cardak/` (add it to the AGENTS.md files table).
  No cron job, no timer, no new systemd unit: a running deck or daemon fetches when the cached
  copy is older than a day, in a worker, with a short timeout. Two running at once may both
  fetch; the write is atomic, so that is harmless.
- The download lives in a collector (no cardak imports, like the rest), parses the JSON
  defensively, and on any failure (offline, timeout, bad JSON, missing fields) keeps the cached
  copy and logs one line. It sends nothing but the GET: no key, no machine details.
- Per model: input, output, 5-minute cache write, 1-hour cache write and cache read rates, and the
  long-context rates where the list has them. A model missing a field it needs counts as unpriced.
- **Fallback:** a built-in table in `core` (taken from Anthropic's pricing page at build time)
  for when there is no cache yet and the download fails, e.g. the first run offline. The waiting
  summaries keep their own table in `core/summary.py`; whether they switch to the downloaded
  prices is the builder's call.
- A dated or suffixed id (`claude-…-20251001`, `[1m]`) is matched to its base model.
- A reply whose model has no price is left out of the dollar figure, and the header says so,
  for example `$412+ this month at API prices`, with the unpriced models named in the `i` panel
  or event log (builder's call where). It never shows as $0.
- **Surcharges:** each reply's `usage` carries a `speed` field (fast mode), and its input total
  shows whether a request crossed a long-context threshold. Measure both on live transcripts and
  price them where the pricing page gives a rate; record in AGENTS.md whatever can't be priced.
- **Advisor model:** records can carry `advisorModel`, and `usage.iterations` is a list. Check on
  live transcripts whether another model's tokens hide there, and price them as that model rather
  than as `message.model`.

### Attributing to projects

- A reply belongs to the project its line's `cwd` resolves to, through the same
  `ProjectResolver` the panes use (configured project, then git root, else Other). Resolve at
  read time, from the distinct `cwd`s of this month's rows, so a project added or renamed
  later takes its sessions' spend with it. A `cwd` that no longer exists falls back to Other
  (or to a configured project whose path contains it).

### Showing

- The snapshot (`DeckSnapshot` / `Group`) carries the month total and each group's figure;
  `ui` and `web/view.py` only format it. `web/view.py` whitelists the new fields.
- Money format: `$0.42` under $10, then whole dollars with thousands separators (`$37`,
  `$1,204`). Builder may adjust.
- Before the first pass has finished, the header shows no spend part (not "$0"). A machine with
  no transcripts at all shows `$0 this month at API prices`.
- With a filter active, the header's match text stays as it is today; the spend part is
  appended to it too (builder's call if it gets crowded).

## Layering and checks

- `collectors` parse; `core` prices, stores and attributes; `ui` and `web` format. No new import
  from `core` into `ui`/`web`/etc.
- Tests use fixtures and the temp XDG dirs from `tests/conftest.py`, never the real
  `~/.claude`. Point the transcripts root at a fixture dir in tests.
- Pin the enola baseline before editing; after the change, split any new complexity outlier or
  record it in AGENTS.md "Architecture checks", as previous milestones did.
- CHANGELOG `## Unreleased` → Added: one line for the person using the deck.

## Left to the builder

- Module names, table and column names, how often the daemon and deck run a pass.
- Whether the first pass takes in this month's existing transcripts (preferred if it stays off
  the UI thread and finishes in reasonable time) or starts at each file's end.
- Exact placement on the phone page, within "near the machine numbers" and "on each project card".
- Where unpriced models are named.
