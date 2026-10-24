# M6: checkpoint & recap — design

Issue #64. PLAN.md §9, §10 (UI sketch), §11 M6.

## Why

Work on a project stops mid-thought: a session ends, the machine crashes (M5), or the user
switches to another project for a few days. Coming back, the state is spread over a closed
conversation, git, and GitHub, and rebuilding it costs the first half hour. M6 makes the agent
write down where things stand while it still knows (`/checkpoint`), and gives a short briefing
on return (`/where-were-we`), so the next session, the user, or a different agent can pick up
from the same note.

Both work without the deck. The deck only makes them one key away.

## Goals

1. **`/checkpoint`**, a Claude Code skill: appends a dated entry to the project's handoff file
   with the goal, what got done, decisions made, what is waiting on the user, the next concrete
   step, and branch / issue / PR links.
2. **`/where-were-we`**, a Claude Code skill: reads the latest entry, what changed in git since,
   and open issues / PRs, and gives a short briefing.
3. **`cardak skills install`** puts both where Claude Code finds them, after asking.
4. **Deck: Checkpoint** (`c`) sends `/checkpoint` to the selected agent pane.
5. **Deck: Recap** shows a project's latest handoff entry and what changed since, without
   starting anything.

## Non-goals

- Writing checkpoints automatically (on a timer, on `SessionEnd`, before a crash). A checkpoint
  is written by the agent that did the work, when asked.
- Agents other than Claude Code.
- Syncing the handoff file anywhere, or reading it from GitHub.

## Anti-goals (a failure even if it works)

- **Typing into an agent the user didn't choose, or at a moment it would answer something.**
  Text sent into a pane showing a permission prompt or an `AskUserQuestion` menu can approve a
  tool or pick an answer. The deck sends only to a session sitting at its input prompt, and only
  after the user confirms the pane.
- **A handoff file that ends up in a commit or a public repo by accident.** It can hold notes the
  user wouldn't publish.
- **cardak editing `~/.claude` without asking**, or overwriting a skill the user wrote.
- **The deck launching agents, or running `gh` / network calls on its poll.**

## The handoff file

`<dir>/.cardak/handoff.md`, as PLAN §9 says, where `<dir>` is the git top level of the session's
directory (`git rev-parse --show-toplevel`), or the directory itself outside git. **Each git
worktree keeps its own note** (the user's choice): it goes away when the worktree is removed, and
the deck looks for it in each pane's directory (see Recap). The skills keep it out of git without touching the
project's own `.gitignore`: whoever creates `.cardak/` also writes `.cardak/.gitignore` containing
`*`, which ignores the directory, itself included. (My call.)

Markdown, newest entry **last** (append-only; the file is a log). Each entry:

```markdown
## 2026-10-24 14:10 · 57-history

**Goal:** …
**Done:**
- …
**Decisions:**
- …
**Waiting on you:**
- … (or "nothing")
**Next step:** …
**Links:** PR #62, issue #57
```

- The heading is `## YYYY-MM-DD HH:MM` in local time, then ` · <branch>` when in a git repo. This
  heading is the only thing the deck parses: an entry runs from its heading to the next `## ` or
  the end of the file. Everything inside is free text and is shown as written.
- The file starts with a one-line preamble the first `/checkpoint` writes:
  `# Handoff notes (written by /checkpoint, read by /where-were-we and cardak)`.
- The path is fixed. PLAN §7's `notes_file` setting is dropped (the skills can't read cardak's
  config), and PLAN §7 is updated to say so. (My call.)
- Entries are never rewritten or reordered by either skill or by cardak. cardak only reads it.

## The skills

Shipped inside the package (`src/cardak/claude/skills/<name>/SKILL.md`, so they are in the wheel;
AGENTS.md's layout line moves there). Plain Claude Code skills: frontmatter `name` and
`description`, then instructions. They do not call cardak and work on a machine without it.

**`/checkpoint`**
- Writes the entry from the current conversation, plus `git branch --show-current`,
  `git status --short` and, when `gh` works, the current branch's PR. Short: what a person needs
  to resume, not a transcript. "Waiting on you" lists only things the user has to decide or do.
- Takes an optional argument as a note to include (`/checkpoint before the refactor`).
- Never commits, stages or pushes. Creates `.cardak/` and its `.gitignore` if missing.
- Ends by saying where it wrote and showing the entry.
- The directory is the git top level if in a repo (a worktree's own), else the current directory.

**`/where-were-we`**
- Reads the latest entry; with none, says so and briefs from git and GitHub alone.
- Gathers: `git log` since the entry's time (one line each), `git status --short`, current branch;
  when `gh` works, open PRs by the user and open issues assigned to the user or mentioned in the
  entry's links.
- Briefing, about 15 lines at most: where you were (goal and next step from the entry), what
  changed since, what is waiting on you, and the suggested next step. It does not start the work.

### Installing them

`cardak skills install | uninstall | status`, alongside `cardak hooks …`:

- Target: `~/.claude/skills/<name>/SKILL.md`. Asks before writing (or `--yes`), like
  `cardak hooks install`.
- Writes only over a file carrying cardak's marker (an HTML comment line
  `<!-- cardak: generated -->` right after the frontmatter), else refuses and names the file;
  `--force` overrides. Writes only when content changes, atomically.
- `uninstall` removes only marked files, and the skill directory if that leaves it empty.
- `status`: installed, outdated (content differs from this version's), not installed, or "not
  cardak's".
- The deck shows a one-time hint when the skills aren't installed and the user presses `c`.

## Deck

### Checkpoint (`c`)

- On a pane row with a Claude session, or on a project row whose project has exactly one; on a
  project with several, it says to pick the pane.
- Allowed only when the session is **at its input prompt**, decided from its current
  `SessionState` alone (no new column, no `events` query; my call):
  - allowed: `idle` (including a fresh session after `SessionStart` or `/clear`), and `waiting`
    with kind `finished`, or kind `question` from a `Stop` whose message ends in a question (a
    slash command there runs as a command, it doesn't answer);
  - refused, with the reason in a notice: `working`; `waiting` with kind `permission`; a pending
    `AskUserQuestion` menu (`current_tool == "AskUserQuestion"`); no hook data for the session
    (says to run `cardak hooks install`).
- Asks first, in a small dialog: "Send /checkpoint to pane 12 · webshop · claude?"
  Enter sends, Esc cancels. The dialog also says "Anything already typed in that pane's input is
  sent with it." (the user's choice: nothing is cleared; hooks can't see a draft). (My call: the read-only rule in AGENTS.md otherwise holds, and this is
  the one place the deck types into an agent.)
- Sends with `wezterm cli --no-auto-start send-text --pane-id N --no-paste "/checkpoint\r"`, through
  `collectors/wezterm.py`. Whether `\r` submits in Claude Code's input box is to be verified live
  in a throwaway GUI before the plan relies on it.
- Does not jump to the pane. The session then shows as working, as any prompt would.

### Recap (PLAN's `[r]`)

`r` is Refresh today. Recap takes `r`, and Refresh moves to `R` (polling makes it rarely needed).
(My call.)

- Which notes it reads:
  - on a pane row: the note at that pane's directory's git top level (or the directory itself);
  - on a project row: the note at the project's path's top level, plus the note at each of the
    project's panes' top levels (worktrees included), each place once. With several, it shows the
    newest entry and lists the others underneath (directory, branch, age) to open with Enter.
- Opens a screen for that note:
  - the latest handoff entry, rendered as Markdown, with its age ("3 days ago");
  - "Since then": commits since the entry's time (newest first, up to 20, one line each), the
    current branch, and how many files are changed or untracked;
  - with no handoff file: "No checkpoint yet. Press c on an agent pane to write one." and the
    git part alone.
- Reads only local files and runs only `git` (read-only, `--no-optional-locks`), with a timeout.
  No `gh`, no network: the full briefing with GitHub is `/where-were-we`.
- `Esc` closes. Read in a worker so a slow git doesn't freeze the deck.

### Where the code goes

- `collectors/handoff.py`: find and parse `.cardak/handoff.md` (entries by heading).
- `collectors/git.py`: the read-only git calls (log since, status, branch).
- `collectors/wezterm.py`: `send_text`.
- `core/checkpoint.py`: whether a session can take a checkpoint (pure, from session state).
- `claude/skills.py` (or alongside `claude/install.py`): the skills installer.
- `ui/recap.py`: the recap screen; the confirm dialog next to the deck's existing modals.

## Left to the builder

The skills' exact wording, within the content and limits above. How the recap screen is laid out.
The test fixtures (a handoff file with several entries, one with a missing branch, one with odd
headings inside entries). How the skills installer shares code with the hooks installer.
