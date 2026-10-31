# PM view (#68)

## Intent

On a Monday morning the user looks across the 3–5 projects they have in cardak and picks what to
work on next, then starts a Claude session per project. The PM view is for **choosing** work: every
open issue of every project, what is already in progress, and where each project left off last
week. It is not a tracker and changes nothing on GitHub.

## Goals

- Two screens in the deck, both in layout **A** (chosen from mockups): a **band per project**, each
  band with three columns side by side:
  - **todo**: every open issue not in onit, **most recently updated first**, each card showing the
    number, title, **labels** and last update ("2 h ago").
  - **onit**: open issues being worked on, and open PRs that close no open issue (rules below),
    each card showing the branch or the open PR, and the state of a Claude session on that branch.
  - **done · 7 days**: an LLM summary in bullet points of what was merged and closed in the last
    7 days, each bullet citing its PRs (`#203`), or "Nothing done this week."
- **All projects** (`P`): one band per project in the deck's project order. ↑/↓ moves between
  bands, ←/→ between columns; each list scrolls on its own. Nothing is cut: a project with
  hundreds of issues scrolls.
- **One project** (`p` on a project row, a pane row or a "Waiting on you" row, which count as their
  project): the same band, full height.
- `Enter` on a card opens the issue or PR in the browser; on a done bullet, its first PR. `R`
  refreshes from GitHub. `Esc` closes.
- The header says when GitHub was last read ("fetched 12 min ago") and any problem.
- Sparing with GitHub: it is read only while the deck runs, and only on the triggers below.

## Non-goals

- No writes to GitHub, no starting sessions from a card, no moving cards. The deck stays read-only.
- No per-project GitHub accounts: one `gh` login reaches every project (the user confirmed).
- No GitHub reading in `cardak daemon`: the PM view works while the deck runs (the user: "the PM
  feature works only when cardak is on"). With the deck closed the phone shows the last board with
  its age, and nothing refreshes it.
- No matching of issue numbers in what the user typed into sessions (noisy; branches and PRs say
  it better).
- No GitHub token handling in cardak: `gh` owns authentication.

## Anti-goals

- Spending the GitHub rate limit or an LLM call on every poll.
- A stale view that looks fresh: the fetch time is always on screen.
- Breaking the deck when `gh` is missing, logged out or offline: the bands say so, the rest of the
  deck is untouched.

## On the phone

The user wants the overview on the phone page too (M7's web view, served by `cardak daemon`).

- The deck writes the board it shows (every project's three columns, card fields, the onit
  sessions' states, the done bullets, fetch times and errors) to a file atomically, whenever it
  changes. The daemon reads that file when it changes and serves it at `GET /api/pm` (Bearer, like
  the others). The daemon never calls `gh`, git or a summarizer for it.
- `web/view.py` whitelists what goes out: names, numbers, titles, labels and their colors, branch
  names, relative times, bullet text, and `https://github.com/` URLs only. No paths.
- The page gets a **Projects** sheet, opened like the Trends sheet: one section per project,
  stacked, each with todo, onit and done one below the other, every card shown (the page scrolls);
  a tap on a card or a bullet's number opens it on GitHub. It fetches `/api/pm` when the sheet opens
  and every 30 s while it is open. The header line shows "fetched N min ago" from the board.
  Phone layout within that is my call; checked at 390×844 and 1180×820 like the other sheets.
- Links are set with `setAttribute` after checking the `https://github.com/` prefix; text through
  `textContent`, as the page's CSP and conventions require.
- Read-only: no refresh from the phone.

## Which repository a project is

`[[project]] github = "owner/repo"` already exists in config (`Project.github`, unused until now).
When it is absent, the repository comes from the project's `origin` remote, read with
`git --no-optional-locks remote get-url origin` (github.com URLs only, ssh or https). A project with
neither gets a band saying "not on GitHub" and no columns.

## Reading GitHub

All `gh` invocation and parsing lives in a new `collectors/github.py` (one interface per external
format). It runs `gh api graphql` with a timeout (a missing `gh`, a non-zero exit or a timeout is a
readable error, never an exception into the UI). Checked 2026-10-31 with gh 2.45: `gh pr list
--json` has no `closingIssuesReferences`, GraphQL does, so GraphQL it is. One fetch per project
reads, paginating at 100:

- open issues: number, title, labels (name, color), updatedAt, url;
- open PRs: number, title, headRefName, url, closingIssuesReferences;
- PRs merged in the last 7 days: number, title, url, mergedAt, closingIssuesReferences;
- issues closed in the last 7 days: number, title, url, closedAt.

That is a handful of requests per project per refresh, against a limit of 5,000 an hour.

## onit

An open issue is in onit when any of these holds:

- a **local branch** of the project or one of its worktrees starts with its number:
  `68-pm-view`, `feat/68-pm-view` (the number right after the start or after the last `/`, followed
  by `-`, `_` or the end);
- an **open PR** closes it (`closingIssuesReferences`), or the PR's head branch starts with its
  number by the same rule.

Local branches come from git (`for-each-ref refs/heads`, `worktree list --porcelain`) through
`collectors/git.py`, read-only, no network. They are re-read whenever the screen is open and the
deck polls, so a new `68-…` branch moves the card at once, without asking GitHub.

**Open PRs that close no open issue** (none in `closingIssuesReferences` and no issue number in
the branch, e.g. `fix-typo`) are onit too, as **PR cards**: number, title, labels, head branch
(the user asked for them). onit is ordered most recently updated first, issue and PR cards mixed
(my call). The PR fetch therefore also reads each open PR's labels and updatedAt.

A card shows the branch (or `PR #207 open`, both when both). When a live Claude session's folder is
on that branch, the card shows its state (working / waiting / idle) the way the deck colors it.

## done · 7 days

- Input: the merged PRs and closed issues of the last 7 days (titles and numbers only, nothing from
  issue bodies or code). Issues closed as "not planned" (`stateReason: NOT_PLANNED`) are left out
  (my call).
- Written by the summary provider chosen under `s` (#47–#49) through the same `Summarizer` protocol,
  with a prompt of its own: a few bullets in plain language, each ending with its PR numbers, or
  an issue's number for an issue closed without a PR (my call). A bullet that cites no number it
  was given is dropped.
- Written again only when that input changes (a hash of it is cached), so an hourly refresh with
  nothing new costs no LLM call.
- **It's an expense** (the user decided): each call is recorded with its provider, model, tokens
  and cost (priced as `core/summary.cost` prices waiting summaries) in a new table (a new table
  keeps `MIN_READER`), and counts toward `[summary] monthly_limit_usd` together with the waiting
  summaries. The `s` screen's spend line includes it. With the cap reached, no call is made.
- When summaries aren't ready (no provider set up, it fails, or the cap is reached), the column lists the merged PRs'
  titles with their numbers instead, and says why in one dim line.
- Nothing merged or closed: "Nothing done this week."

## Refreshing

GitHub is read, per project, only while the deck runs:

- when the deck starts (if the cache is older than 60 minutes) and every **60 minutes**;
- on **`R`**: in the deck it still polls panes at once, and also refreshes GitHub for every
  project; on the all-projects screen every project, on the one-project screen that project (my
  call: the user named the existing refresh key as a trigger);
- shortly after a Claude session **changed GitHub**: the deck reads the hook events cardak already
  stores (`events.payload_json`, `PostToolUse`) and refreshes the session's project when a Bash
  command ran `gh issue …`, `gh pr …`, `gh release …` or `git push`, or an MCP tool whose name
  starts with `mcp__github` ran. Commands typed in a plain shell aren't seen; `R` covers those.
  `cardak hook` is unchanged (its 150 ms budget stays). The session's project is its stored `cwd`
  resolved through the resolver. The deck keeps a rowid watermark; at start it also refreshes any
  project with such an event newer than that project's last fetch, so a push made while the deck
  was closed still counts (my call).

Several triggers within a minute for one repository make one fetch. Fetches and the cache are
keyed by `owner/repo`: projects sharing a repository share one fetch, and each band's local
branches come from its own project's checkout and worktrees (my call). The last fetch of each project,
and its done summary with the input hash, are cached atomically under `~/.cache/cardak/`, so the
screen fills instantly on open and after a restart.

## Errors

Shown in the band (and the header for a deck-wide one), the last good data kept and marked with
its age:

- `gh` not installed: "gh isn't installed".
- not logged in or no access: `gh`'s own message, one line.
- offline or timeout: "can't reach GitHub", with the last fetch's age.

## Constraints

- Layering stays: `collectors/github.py` imports nothing from cardak; the board (classifying,
  sorting, onit rules, done input) lives in `core`; the screens in `ui/`; `cli.py` hands the
  summarizer factory over as it does for `s`.
- Tests never call `gh`, git on real repos, the network or a real provider: recorded GraphQL
  replies go in `tests/fixtures/github/` (made-up repositories).
- The file table in AGENTS.md gains the cache file, the board file and the new table.

## Left to the builder

- The exact look of cards and bands within layout A, and how a band's height is shared on the
  all-projects screen.
- The cache file's name and shape, the timeouts, and the debounce.
- The done prompt's wording and how many bullets at most.
- Where the fetch runs (a worker thread, like recap's git reads).
