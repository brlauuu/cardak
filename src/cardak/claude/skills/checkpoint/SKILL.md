---
name: checkpoint
description: Write down where this project stands (goal, what got done, decisions, what's waiting on the user, the next step, links) in .cardak/handoff.md, so a later session can pick up from it. Use when the user asks for a checkpoint or a handoff note.
argument-hint: "[note to include]"
---
<!-- cardak: generated -->

# Checkpoint

Append one entry to this project's handoff note, so that the user, a later session or another
agent can pick up from here. Write it from what you know of this conversation; it is for a person
resuming the work, not a transcript.

## Where

1. The project directory is `git rev-parse --show-toplevel` (in a git worktree, that is the
   worktree's own directory). Outside git, use the current directory.
2. The note is `<project directory>/.cardak/handoff.md`.
3. If `.cardak/` doesn't exist, create it, and create `.cardak/.gitignore` containing the single
   line `*`, so the note stays out of git without touching the project's own `.gitignore`.
4. If `handoff.md` doesn't exist, start it with this line and a blank line:

   `# Handoff notes (written by /checkpoint, read by /where-were-we and cardak)`

## What to gather

- `git branch --show-current` and `git status --short`, when in a git repo.
- If `gh` is installed and works here, the pull request for the current branch:
  `gh pr view --json number,url,state` (skip quietly if it fails).
- From this conversation: the goal, what got done, the decisions made and why, anything the
  user has to decide or do, and the next concrete step.
- The user's note, if they gave one after the command: $ARGUMENTS

## The entry

Append (never rewrite or reorder earlier entries) a blank line and then one entry in exactly this
form. The heading is the local date and time, then ` · ` and the branch when there is one:

```markdown
## 2026-10-24 14:10 · add-price-filter

**Goal:** Let shoppers filter the listing by price.
**Done:**
- Parser for the price range query, with tests.
**Decisions:**
- Prices are compared in cents, to avoid rounding.
**Waiting on you:**
- nothing
**Next step:** Add the filter control to the listing page.
**Links:** PR #42, issue #40
```

- Keep it short: a few lines per section, only what someone needs to resume.
- "Waiting on you" lists only what the user must decide or do; write "nothing" when there is
  nothing.
- "Links" are branch, issue and PR references; leave the line out when there are none.
- Use `##` only for the entry's heading. Inside the entry, use bold labels and lists.

## Rules

- Never commit, stage or push the note or anything else. Don't edit the project's `.gitignore`.
- Don't include secrets, tokens or credentials.
- Finish by saying where you wrote it and showing the entry.
