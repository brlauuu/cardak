# Changelog

Every merged pull request adds a line under **Unreleased**. A release moves
those lines under a version heading and tags that commit. Versions follow
[Semantic Versioning](https://semver.org/) and stay below 1.0 while the deck
is being built: a minor bump for a new user-visible capability, a patch bump
for fixes, dependencies and internal work. See "Versioning and releases" in
`AGENTS.md`.

## Unreleased

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
