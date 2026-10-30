# Plan limits on the deck (#70) — design

## Intent

The user runs Claude Code on a Pro/Max plan, which has a 5-hour limit and a weekly limit. When
they're close to either, they decide which project gets what's left, using their own heuristics.
cardak's job is only to tell them, at a glance, **how much of each limit is left and when it
resets**, and to make "close to the limit" impossible to miss. Nothing else: no per-project share of
the window, no estimates, no advice (the user said so explicitly).

Success: in the deck's top area and on the phone page, one row shows both limits as bars of what's
left, with the reset time, turning yellow and then red as a limit runs out.

## Where the numbers come from

Measured on 2026-10-30 (Claude Code 2.1.314, docs at code.claude.com/docs/en/statusline.md,
"Rate limit usage"): the only documented machine-readable source is the JSON Claude Code pipes to
the **status line command** on every status line refresh:

- `rate_limits.five_hour.used_percentage`, `rate_limits.seven_day.used_percentage`: 0–100;
- `rate_limits.five_hour.resets_at`, `rate_limits.seven_day.resets_at`: Unix epoch seconds.

The object is present only for claude.ai Pro/Max subscribers (or behind a gateway with a spend
limit), only after the session's first API response; either window may be missing, and a window is
dropped once its `resets_at` has passed. The docs give no version for these fields.

Not used, decided with the user:
- the endpoint `/usage` asks (undocumented: no URL, schema or auth; would mean reading the login
  token and going over the network);
- estimating from transcripts (Anthropic doesn't publish the limits).

So cardak **wraps the user's status line**: it becomes the `statusLine.command`, saves the limits,
and runs the user's original command with the same input. The user accepted this as a workaround and
asked for a follow-up to replace it once Claude Code offers a supported way: **#97**. README and
AGENTS.md say it is a workaround and link #97.

## Freshness

The user doesn't need the figures to be fast or exact to the minute: a reading from "every once in a
while" is fine, and they'd rather glance and decide (said 2026-10-30). So the wrapper saves at most
once a minute unless a window changed, and the deck picks the file up on its next poll. There's no
refresh action: cardak can't ask Claude Code for new numbers, only re-read the last reading, which
every poll already does; the age label says how old it is. The latency budget below exists only so
the user's own status line doesn't get slower.

## Goals

1. `cardak statusline install` / `uninstall` / `status`, in the same style as `cardak hooks …`
   (`claude/install.py`): show the change, ask (or `--yes`; no keyboard → write nothing and say so,
   as the other installers do since #80), back up `~/.claude/settings.json` to
   `.cardak-backup-<time>` first, change nothing but `statusLine`.
2. `cardak statusline` (run by Claude Code): read stdin, save the two limits, run the original
   command with the same stdin, print its stdout, exit with its exit code. The user's status line
   must look and behave exactly as before.
3. The deck shows one row with both bars; the phone page shows the same row.
4. Colours by what's left: normal at 20% or more, yellow below 20%, red below 10% (thresholds are
   my call; constants in one place).

## Non-goals

- Per-project share of a window, or any other figure to help choose (the user declined).
- Notifications or sounds when a limit gets low (not asked for).
- History of the limits over time.
- The `spend_limit` object or API-key / Console users' limits.

## Anti-goals

- **Breaking the user's status line.** Any failure in cardak's part (bad JSON, no `rate_limits`, an
  unwritable state dir, an exception) must still run the original command with the original input
  and print its output; errors go to `hook.log` as the hook's do.
- Adding noticeable latency to every status line refresh.
- Showing an old figure as if it were current.
- Touching anything in `settings.json` other than `statusLine`.

## How it works

### Install (`claude/install.py` or a sibling module under `claude/`, importing nothing from cardak)

- The wrapped command is `<abs path to cardak> statusline --then <shlex-quoted original command>`,
  so the original lives in `settings.json` itself and `uninstall` can restore it exactly
  (`shlex.split`). With no status line before, the command has no `--then` and prints nothing.
- Only `statusLine.command` changes; `type`, `padding`, `refreshInterval` and any other keys are
  kept. A `statusLine` whose `type` isn't `"command"` is refused with a message (none exist today
  besides `command`, but the installer shouldn't guess).
- Already wrapped → "already installed", nothing written. `uninstall` puts the original command back
  (or removes `statusLine` if cardak added it) and leaves the rest untouched.
- `status`: installed (and what it wraps) / not installed, and when the last limits were read.
  If the user or Claude Code's `/statusline` later rewrites the command, it's simply "not
  installed" again; `uninstall` then says there's nothing of cardak's to remove and writes nothing
  (my call). The deck doesn't read `settings.json`; the reading's age label is what shows a
  wrapper that stopped running.
- Scope is the user file `~/.claude/settings.json` only (my call). A project's
  `.claude/settings*.json`, managed settings or `CLAUDE_CONFIG_DIR` can set their own `statusLine`
  and override it; in those sessions no limits are read. README says so; cardak doesn't scan or
  edit those files.

### Wrapper (`cardak statusline`)

- **Budget:** like `cardak hook`: dispatched in `cli.main` before argparse, imports only what it
  needs (no Textual, no SQLite store, no summarizer); `tests/test_cli.py` guards it the same way.
  cardak's own part should add well under 100 ms median to a refresh; measure it and record the
  number in AGENTS.md.
- Parsing the input belongs in `collectors/` (Claude Code's format, one interface per external
  format): it returns each window's used % and reset time, or nothing.
- Saves `{"read_at", "five_hour": {"used", "resets_at"} | null, "seven_day": … }` atomically to
  `~/.local/state/cardak/limits.json`. A reading where `rate_limits` is absent writes nothing (it
  says nothing, it isn't "0%").
- **Several sessions write it, and an idle one keeps handing over the figure from its last API
  response** (my reading of the docs: the object appears "after the first API response", so it is a
  per-session copy; check it live if a scratch session allows). So the wrapper **merges** per
  window instead of overwriting (my call): a later `resets_at` replaces the stored window; the same
  `resets_at` keeps the higher used % (use only grows within a window); an earlier one is ignored.
  Merging is a read-modify-write: lock the file (`flock` on a sibling lock file) so two refreshes
  can't lose each other's update.
- `read_at` is when cardak last got a reading at least as new as the stored one (it set or matched
  the stored figure), not the last refresh of any session (my call): an idle session repeating an
  older, lower figure doesn't make the row look fresh. To keep refreshes cheap, the file is written
  only when a window changed or the stored `read_at` is over 60 s old.
- Runs the original with `sh -c` (as Claude Code does), passing the exact stdin bytes and the
  environment through, and relays stdout and the exit code. It doesn't add its own timeout: Claude
  Code already manages the status line process.

### Showing it

- `LiveSource` reads `limits.json` when its mtime changes and puts it on the snapshot (one more
  field, e.g. `DeckSnapshot.plan_limits`). Read-only; the deck and the daemon both do it.
- Per window: `left = 100 − used`. A window is shown as "—" when it's missing or its `resets_at`
  has passed (it has reset; what's left is unknown until the next reply).
- Reading age (`read_at`, above). When it's over 15 minutes old the
  row ends with "· read 25 min ago" (my call), so a stale figure is never taken as current. With no
  file at all, the row is hidden (the user hasn't installed it, or isn't on a plan).
- **Deck:** one row under the machine line (`#machine`), its own `Static`, coloured:

  ```
  5h ███████████░░░░ 27% left · 16:40   week ██████░░░░░░░░░ 61% left · Mon 09:00
  ```

  The bar shows what's left (full = untouched). The reset is a time today, a weekday and time within
  the week, local time. The exact glyphs and widths are the builder's, but both limits stay on one
  row at the deck's usual width.
- **Phone:** `snapshot_json` gains the same data (left %, reset time, read age; no paths), and the
  page draws the same row under its machine line, coloured with CSS classes (the CSP allows no
  `style=`). It's whitelisted like everything else in `web/view.py`.

## Constraints

- Layering and the "one interface per external format" rule in AGENTS.md: parsing in `collectors/`,
  the settings writer under `claude/` importing nothing from cardak, `core` never importing `ui` or
  `web`.
- `claude/install.py` stays the only writer under `~/.claude/` (or its sibling under `claude/`).
- Tests never touch the real `~/.claude/settings.json`, state dir or user's status line; they pass
  scratch paths and record a sanitized status line input as a fixture in `tests/fixtures/` (shape
  from the docs; verify against a live capture in a scratch session if possible, without changing
  the user's settings).
- The live check runs against scratch `HOME`/XDG dirs, never the user's settings; the user installs
  it on their own machine.
- Docs: README (install line and the workaround note → #97), AGENTS.md (a section like #86's, the
  files table rows for `limits.json` and the `statusLine` entry in `settings.json`), CHANGELOG
  Unreleased → Added.

## Left to the builder

- Module names, the snapshot field's name, glyphs and bar width.
- Whether `status` prints the reading's numbers.
- How a narrow deck shortens the row (but never to two rows).
