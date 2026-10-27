# Changelog

Every merged pull request adds a line under **Unreleased**. A release moves
those lines under a version heading and tags that commit. Versions follow
[Semantic Versioning](https://semver.org/) and stay below 1.0 while the deck
is being built: a minor bump for a new user-visible capability, a patch bump
for fixes, dependencies and internal work. See "Versioning and releases" in
`AGENTS.md`.

## Unreleased

## 0.10.0 — 2026-10-27

### Added
- See the deck on your phone or iPad over your Wi-Fi: what's waiting on you and why, each project's panes and RAM, and what a session needs, refreshed every few seconds and clear about when the computer can't be reached. It only shows; nothing on it can act on your agents or mark a wait seen. Run `cardak web setup` once: it creates a private link and a QR code to open on each device. The background service serves it, so run `cardak resources install` if you haven't. (#80)

### Fixed
- `cardak hooks install`, `resources install`, `skills install` and `web setup` no longer crash when there's no keyboard to answer their question (a `!` command, a pipe): they write nothing and tell you to run them in a terminal or pass `--yes`. (#80)

## 0.9.2 — 2026-10-26

### Fixed
- A project configured through a symlink is recognised whichever way the pane got there, for grouping, WezTerm themes and the zsh tint. (#14)
- Saving a scheme in the theme picker no longer flashes the old colors first. (#22)
- Two projects with the same path get the same scheme in WezTerm as in the deck (the first one listed), and the deck says which one is ignored. (#23)
- An empty scheme cache is checked again instead of leaving the picker empty. (#24)
- Opening the theme picker while the deck is still listing schemes no longer runs the listing twice, and tints get their colors right after the picker's listing. (#25)
- `cardak themes zsh` prints the files it couldn't write, as `cardak themes apply` does. (#27)
- A project whose path has a tab or newline is left out of the tints file instead of breaking it, with a warning. (#28)
- A bug in cardak shows as "cardak hit an internal error", not as WezTerm being unreachable. (#12)
- The config error banner names `projects.toml` once. (#13)
- A WezTerm window closing at the wrong moment no longer shows one poll as an internal error. (#15)
- Typing in find while the deck refreshes keeps the cursor on the best match. (#16)
- With `cardak.lua` installed via `--dir`, jumps raise windows without the install hint, and theme files are written next to it. If you installed it that way before, run `cardak wezterm install --dir <that dir>` once more so cardak remembers where it is. (#19)
- The header uses `·` like the rest of the deck. (#21)
- After jumping from "Waiting on you", the cursor lands on that pane in its project, not at the top. (#39)
- In waiting-only mode (`w`), find counts its matches correctly. (#40)
- Without an Anthropic key, the `i` panel no longer shows a summary spend line. (#52)
- A summaries database problem says so, instead of "agent status unavailable". (#53)
- A deck left running into a new month tells you again when the summary limit is reached. (#55)

## 0.9.1 — 2026-10-25

### Fixed
- A Claude session compacting its context no longer shows as idle while it's still working, or drops out of "Waiting on you". (#36)
- Looking at a waiting session no longer marks a newer wait from the same session as seen. (#37)
- Sessions that crashed without ending are cleared after 7 days, so a reused pane no longer picks up an old session's status. (#38)
- Enter in the `i` panel jumps to the pane for every session, not only those started in a WezTerm pane cardak could see. (#41)
- `cardak hook` notes in `hook.log` when Claude Code sends something it can't read or doesn't know. (#46)
- When a waiting session switches to a different kind of ask, its summary is rewritten for the new ask. (#56)
- Notifications show text like `cat <in >out && x` as written; KDE used to read it as HTML and garble or drop it. (#42)
- `cardak hooks install` keeps `settings.json`'s indent; a file Claude Code wrote comes back byte for byte apart from cardak's entries. (#44)
- Two WezTerm windows from separate processes no longer write the focus history through the same temporary file. Re-run `cardak wezterm install` to get it. (#43)
- From this version on, going back to an older cardak keeps agent status working as long as the database's newer parts don't matter to it, instead of refusing the whole file. Versions up to 0.9.0 still refuse a newer file. (#54)

## 0.9.0 — 2026-10-25

### Added
- `/checkpoint` and `/where-were-we` for Claude Code: one writes down where the work stands (goal, what got done, decisions, what's waiting on you, the next step) in the repo's `.cardak/handoff.md`, kept out of git; the other briefs you from it, git and your open issues and PRs when you come back. Run `cardak skills install` once. (#64)
- `c` in the deck sends `/checkpoint` to the selected Claude session after asking, only while it's at its prompt. (#64)
- `r` shows a project's or pane's latest checkpoint and what changed in git since, across its worktrees. (#64)

### Changed
- Refresh moved from `r` to `R`. (#64)

## 0.8.0 — 2026-10-24

### Added
- `h` opens 30 days of history per project: RAM (or CPU with `c`) now, median and peak, the number of alerts, and a calendar strip of daily peaks. A day opens that project's 30-day plot with the alert threshold and that day's alerts. The background service records it; run `cardak resources install` if you haven't. (#57)
- `e` opens the event log: every resource alert, crash report and service start, newest first, with quiet repeats folded into one line. (#57)
- When a WezTerm window closes because memory ran out, a notification says what happened: which process the kernel killed, in which project and pane, how fast it grew, the biggest users at that moment, and how to keep the window open next time. The deck shows it at the top until you press `d`. (#57)

## 0.7.0 — 2026-10-23

### Added
- A desktop notification when memory runs low or a project or pane gets too big, grows too fast or stays too busy, naming the project, pane and command; at most one every 10 minutes per project, and it keeps watching with the deck closed. Run `cardak resources install` once. (#57)
- `cardak resources install` writes the thresholds it uses into `[resources]` in projects.toml, worked out from your machine, and tells you how to keep a WezTerm window open when the kernel kills one of its processes for lack of memory. (#57)

### Changed
- The deck reads process numbers straight from `/proc`, which takes about a seventh of the CPU it did. (#57)

## 0.6.0 — 2026-10-22

### Added
- Every project and pane shows its RAM with a short graph of the last few minutes, and the machine's CPU and RAM sit at the top, with a warning when memory runs low. A pane or project over a threshold turns red; the thresholds come from your machine, or from `[resources]` in projects.toml. (#33, #57)
- Docker containers count toward the project they belong to (by their Compose folder or a mounted project folder), and a `docker build` toward the project that started it. MCP servers under Claude show as their own line. Everything else is "Other", so the numbers add up to the machine. (#57)

## 0.5.0 — 2026-10-21

### Added
- Each session in "Waiting on you" can say what it did and what it needs from you, in one line written by Claude Haiku, after a tag: approve, decide or review. Put a workspace-scoped Anthropic API key in `~/.config/cardak/anthropic_key` (readable only by you) to turn it on; the deck says when summaries are failing; `[summary] monthly_limit_usd` caps the cost (default $5). (#30)

## 0.4.0 — 2026-10-21

### Added
- The deck shows which Claude Code session is waiting on you, and why. Each Claude pane gets a dot (orange: waiting, green: working, grey: idle), and a "Waiting on you" group at the top lists waiting sessions oldest first, with the reason: a tool to approve, a question for you, or a finished turn. Run `cardak hooks install` once; it backs up `~/.claude/settings.json` and asks before writing. (#32)
- One desktop notification and one sound when a session starts waiting, even with the deck closed, and none if you're already looking at that pane. Pick a sound, `random` or `off`, or add your own in `[notify]` in projects.toml. (#32)
- `i` on a Claude session shows what it needs, your last prompt and what the turn changed; `t` there opens the full transcript. `w` shows only the sessions waiting on you. (#32)
- Focusing or jumping to a waiting session marks it seen. Re-run `cardak wezterm install` once so the deck knows which pane you're looking at. (#32)

## 0.3.0 — 2026-10-20

### Added
- Per-project color schemes: each WezTerm window takes its project's scheme and switches within a second when you change directory. Set `theme = "…"` in projects.toml, or press `t` on a project in the deck to pick from all built-in schemes with a live preview. Re-run `cardak wezterm install` once to enable it. (#8)
- Project groups in the deck use a color from the project's scheme when no `accent` is set. (#8)
- `cardak themes zsh` installs an optional zsh hook that tints each pane's background by project, so split panes of different projects stay distinguishable. (#8)

## 0.2.0 — 2026-10-20

### Added
- Press Enter on a pane (or click it) to jump there: cardak selects its tab and brings its window to the front. Run `cardak wezterm install` once and add the line it prints to your `wezterm.lua` to enable the window raise. (#5)
- Press `/` to fuzzy-find a pane by project, program, title or folder; Enter jumps to the best match and Esc shows everything again. `j`/`k` move up and down. (#5)

## 0.1.0 — 2026-10-20

### Added
- `cardak` opens a live view of every WezTerm pane grouped by project, with the CPU and memory of everything running in each pane, including builds an agent started. Repos you haven't configured are grouped by their git root, and `a` adds one to your projects. (#2)
