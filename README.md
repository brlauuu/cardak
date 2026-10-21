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
The module raises the window you jump to, applies each project's color scheme, and tells
the deck which pane you're looking at. After upgrading cardak, run the command again when
the deck asks you to.

`cardak themes zsh` adds an optional zsh hook that tints each pane's background by project.

## Claude Code status

```sh
cardak hooks install
```

This adds cardak's hooks to `~/.claude/settings.json`. It shows what it will add, backs the
file up next to itself, and asks before writing. Your own hooks are left as they are.
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

## Development

See `AGENTS.md` for conventions and `PLAN.md` for the roadmap.

```sh
uv run pytest
uv run ruff check && uv run pyright
```
