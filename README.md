# cardak

A project-centric deck for WezTerm panes and Claude Code sessions. It groups every pane by
project, jumps to a pane with Enter, gives each project its own color scheme, and shows
which Claude Code session is waiting on you, and why.

Linux and WezTerm only. Developed on Kubuntu 24.04 (KDE Plasma, X11).

## Install

```sh
uv tool install git+https://github.com/brlauuu/cardak
cardak
```

The deck lists every WezTerm pane grouped by project. Projects come from
`~/.config/cardak/projects.toml`; repos you haven't configured are grouped by their git root,
and `a` adds one.

```toml
[[project]]
name  = "webshop"
path  = "~/repos/webshop"
theme = "Catppuccin Mocha"   # optional; or press `t` in the deck
```

## Jumping and themes

```sh
cardak wezterm install
```

This installs `~/.config/wezterm/cardak.lua` and prints one line to add to your
`wezterm.lua`: `require('cardak').apply(config)`. cardak never edits `wezterm.lua` itself.
The module raises the window you jump to, applies each project's color scheme, names each
tab after its project (`webshop · ✳ Fix the cart`), and tells the deck which pane you're
looking at. Tabs outside a project, and tabs you've titled yourself, keep WezTerm's title.
WezTerm uses only one tab-title handler; if you have your own, use
`require('cardak').apply(config, { tab_titles = false })`. After upgrading cardak, run the command again when
the deck asks you to.

`cardak themes zsh` adds an optional zsh hook that tints each pane's background by project.

## Claude Code status

```sh
cardak hooks install
```

This adds cardak's hooks to `~/.claude/settings.json`. It shows what it will add, backs the
file up next to itself, and asks before writing. Your own hooks are left as they are, and so is
the rest of the file's content and its indent; only hand formatting such as an array kept on one
line is spread over several.
`cardak hooks status` checks it, and `cardak hooks uninstall` removes only cardak's entries.
Sessions started before the install report nothing. Claude Code also holds hooks back in a
folder until you accept its "trust this folder" prompt.

In the deck:

- Each Claude pane has a dot: **orange** waiting on you, **green** working, **grey** idle.
- **Waiting on you**, at the top, lists waiting sessions oldest first, with the reason:
  *Approve a tool* (and which), *Your answer* (it asked something), or *Turn finished*.
  Enter jumps there. Jumping to a session, or looking at its pane for a second, marks it
  seen.
- `w` shows only the waiting sessions. `i` on a session shows what it needs, your last
  prompt and what this turn changed. `t` there opens the full transcript.

When a session starts waiting, you get one desktop notification and one sound, even with the
deck closed. You get none if you're already looking at that pane. Configure it in
`projects.toml`:

```toml
[notify]
desktop = true                 # notify-send
sound = "random"               # "random", "off", or a name: chime, pluck, bell, blip, drop
sounds_dir = "~/sounds/cardak" # optional: your own .wav/.oga/.ogg files join the pool
```

cardak keeps its state in `~/.local/state/cardak/`.

## Waiting summaries

With an Anthropic API key, each line in "Waiting on you" says what the session did and what
it needs from you, written by Claude Haiku, after a tag for the kind of ask:

```
● api-gw · claude  4m  approve  Fixed the retry bug; wants to run the full test suite
● webshop · claude  2m  decide  Migrated checkout; keep or delete the old /checkout route?
● notes · claude  30s  review  Split parser.py into 3 modules; tests pass, nothing asked
```

The tag comes from cardak itself, not the model. Until a summary arrives (a second or two),
or when there is none, the line shows the raw reason as before; `i` always shows the raw
reason next to the summary.

Put your key in a file only you can read:

```sh
mkdir -p ~/.config/cardak
(umask 077; printf '%s\n' 'sk-ant-…' > ~/.config/cardak/anthropic_key)
```

Use a key scoped to a workspace (Anthropic Console → Workspaces); a key without one is
refused by the API. If summaries stop appearing, the deck says why in its warning line.

cardak uses this file only; `ANTHROPIC_API_KEY` is ignored. With the key in place, each
time a session starts waiting (and you aren't already looking at it), cardak sends
Anthropic: the project name, your last prompt, what Claude said in the current turn, the
tool call waiting for approval, and the turn's counts and edited file names. Never the
whole transcript.

One summary costs about $0.0002, so a busy month comes to a dollar or so. A monthly cap
stops summaries when it's reached:

```toml
[summary]
monthly_limit_usd = 5    # 0 turns summaries off
```

## Resources

Every project and pane shows its RAM, a small graph of the last few minutes, and its CPU
(100% is one core); the machine's CPU and RAM sit at the top of the deck:

```
CPU 38% ▂▃▃▅▄▃   RAM 27.6 / 32.0G ▃▄▅▆▇█  ⚠ 14% free
webshop  ~/repos/webshop  RAM 3.1G ▂▂▃▃▃  CPU 45%
  ● claude · checkout  .  RAM 1.2G ▃▃▃▃▃  CPU 12%
    └ MCP servers  RAM 412M  CPU 1%
    docker: webshop-db-1  RAM 1.5G  CPU 30%
Other  RAM 9.8G ▅▅▅▆▅  CPU 61%
    outside WezTerm  RAM 9.5G  CPU 58%
```

- A project counts its panes, the MCP servers under its Claude sessions, its Docker containers
  (by their Compose folder, or a project folder they mount) and a `docker build` started from
  one of its panes. Docker needs your user in the `docker` group; without it, Docker work lands
  in Other.
- **Other** is everything else the machine is using, so the numbers add up to the total.
- A pane or project over a threshold turns red, with a ▲. The thresholds come from your
  machine; set them yourself in `projects.toml` if you like (sizes in GiB):

```toml
[resources]
ram_warn_available_gb = 4.8      # warn below this much available RAM (default 15%)
ram_critical_available_gb = 2.2  # critical below this (default 7%), or when swap is full
pane_ram_gb = 8.0                # a pane or project above this (default 25% of RAM)
pane_cpu_pct = 800               # a pane or project above this (default half the cores)
```

### Alerts with the deck closed

```sh
cardak resources install
```

This installs and starts `cardak.service`, a systemd user service that keeps watching with
the deck (or all of WezTerm) closed. It asks before writing `~/.config/systemd/user/cardak.service`
and fills in `[resources]` from your machine, each value with a comment saying where it came from.
Values you set yourself are kept; `--refresh-defaults` recomputes them all.
`cardak resources uninstall` stops and removes the service.

You get a desktop notification when memory runs low, or when a project or a pane gets too big,
grows fast or stays busy for two minutes, naming what's responsible:

```
cardak · webshop is big
webshop · pytest in pane 12 · 13.9 GB of the project's 15.1 GB · RAM 88% used
```

At most one per project and kind every 10 minutes (`repeat_min`); running out of memory
notifies at once and stays on screen. cardak only tells you; it never stops anything.

When memory runs out, the kernel kills one process, but systemd then closes the whole WezTerm
window that process ran in. `cardak resources install` checks for this and prints the fix:

```sh
mkdir -p ~/.config/systemd/user/app-org.wezfurlong.wezterm-.scope.d
printf '[Scope]\nOOMPolicy=continue\n' > ~/.config/systemd/user/app-org.wezfurlong.wezterm-.scope.d/oom.conf
systemctl --user daemon-reload
```

WezTerm windows opened after that lose only the process that was killed.

### History, the event log and crash reports

With the service running, cardak keeps 30 days of each project's usage:

- `h` shows every project's RAM now, its median and peak, how many alerts it raised, and one cell
  per day coloured by that day's peak (full red: it would have alerted). `c` switches to CPU, `s`
  changes the order. Enter or a click on a day opens that project's 30-day plot with the alert
  threshold drawn in and the day's alerts below it; ←/→ move the day.
- `e` lists every alert, newest first; a repeat that didn't notify you is folded into its line.
- When a WezTerm window closes because memory ran out, you get a notification and the deck shows
  what happened at the top next time you open it (`d` dismisses it). For example:

  > WezTerm was closed at 15:28:48 on Oct 21. The machine ran out of memory (32 GB; swap full).
  > The kernel killed `pytest` (pid 41872) in api-gw, pane 12, which grew from 2.1 GB to 13.9 GB
  > in 6 minutes. systemd then stopped the whole WezTerm window, …

  The details come from the system journal, which you can read if you're in the `adm` or
  `systemd-journal` group. Any other way a window closes is one quiet line in the event log.

## Checkpoint and recap

```sh
cardak skills install
```

This adds two Claude Code commands to `~/.claude/skills/` (it asks first, and never replaces a
skill of that name you wrote yourself; `cardak skills status` and `uninstall` too):

- `/checkpoint` has Claude write down where the work stands: the goal, what got done, decisions,
  what's waiting on you, the next step, and branch / issue / PR links. It appends a dated entry
  to `.cardak/handoff.md` at the top of the repo (each git worktree keeps its own), and keeps that
  folder out of git with its own `.gitignore`. It never commits. `/checkpoint before the
  refactor` adds your note.
- `/where-were-we` reads the latest entry, what changed in git since, and your open issues and
  pull requests (with `gh`), and gives a short briefing.

Both work without the deck. In the deck:

- `c` on a Claude pane (or a project with one) sends `/checkpoint` to it, after asking. Only
  while Claude is at its prompt: not while it's working, waiting for a permission, or showing a
  question. Anything you've half-typed in that pane goes along with it.
- `r` shows the latest checkpoint for a project or pane and what changed since: commits, the
  branch, and uncommitted files. With several worktrees, the others are listed below it. It
  reads only local files and git. (Refresh moved to `R`.)

`x` on a project (or one of its panes) opens a new WezTerm tab in the project's folder, next to
that project's panes, types `claude /repo-cleanup` into it and takes you there, after asking.
`/repo-cleanup` isn't one of cardak's skills: it runs whatever skill of that name you have in
`~/.claude/skills/`. The tab is an ordinary shell and stays open when Claude exits.

## Development

See `AGENTS.md` for conventions and `PLAN.md` for the roadmap.

```sh
uv run pytest
uv run ruff check && uv run pyright
```
