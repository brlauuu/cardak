---
name: where-were-we
description: Brief the user on where this project stands, from the last /checkpoint entry in .cardak/handoff.md, what changed in git since, and open issues and pull requests. Use when the user comes back to a project and asks where they were.
---
<!-- cardak: generated -->

# Where were we

Give the user a short briefing on this project, so they can pick up where they left off. Read and
report; don't start the work.

## What to read

1. The project directory is `git rev-parse --show-toplevel` (in a git worktree, the worktree's own
   directory); outside git, the current directory.
2. The latest entry in `<project directory>/.cardak/handoff.md`: entries start with a heading
   `## YYYY-MM-DD HH:MM` (optionally ` · <branch>`), and the newest is the last one in the file.
   With no file or no entry, say there is no checkpoint yet and brief from git and GitHub alone.
3. In a git repo:
   - `git log --oneline --since="<the entry's date and time>"` (or the last 10 commits without an
     entry);
   - `git status --short` and `git branch --show-current`.
4. If `gh` is installed and works here (skip quietly if not):
   - `gh pr list --author @me --state open`;
   - `gh issue list --assignee @me --state open`, and any issue or PR named in the entry's links,
     with its current state.

## The briefing

At most about 15 lines, in this order:

- **Where you were:** the goal and the next step from the entry, and how long ago it was written.
- **Since then:** commits, uncommitted changes, and anything in the linked issues or PRs that
  moved (merged, closed, new review comments).
- **Waiting on you:** what the entry listed, plus anything new from GitHub.
- **Suggested next step:** one concrete action.

Plain text, no tables. Don't modify files, and don't commit, push or comment on GitHub.
