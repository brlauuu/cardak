# #122: a tabular main view — design

## Why

With eight or so projects open, the deck's main view is hard to scan. Every row packs its numbers
in after its text ("RAM 1.2G (4%) ▁▂▄▆█  CPU 30%"), so they land at a different column on
every line, and "Waiting on you" sits at the top of the same tree as everything else. The user
said what they most need to see at once is **what's waiting on them** and **who is using the
resources**. Spend, session state and context should stay visible, but they matter less.

Success: at a glance, the user can read RAM and CPU down a column and see the heavy project
stand out, and see what's waiting without scrolling the tree.

## What it looks like (wide window)

```
cardak 0.19.0 · 8 projects · 14 panes · 2 waiting · $412 this month   5h ██░░ 27% …  week …
                                         state     RAM                 CPU   ctx   month │ Waiting on you
▾ cardak    ~/repos/cardak                         2.1G (7%)  ▂▄▆▆      34%          $88  │ ● webshop · claude  4m
    ● claude  120-pm-issue  Bash        work      1.2G (4%)  ▂▄▄▆      30%   140k         │   Bash: npm test
    zsh       ~/repos/cardak                       12M (<1%)             0%                │ ● api-gw · claude  12m
▾ webshop   ~/repos/webshop                        3.4G (11%) ▆▆▆▆      12%          $41  │   finished: PR ready
    ● claude  212-coupon                wait 4m    900M (3%)  ▂▂▂▂       1%    88k         │
    node      npm run dev                          2.5G (8%)  ▄▆▆▆      11%                │ CPU 18% ▂▄▆  RAM 21.4 / 31.2G (69%) ▄▆▆
    └ MCP servers                                  180M (1%)             0%                │
▸ api-gw    ~/repos/api-gw                         1.0G (3%)  ▂▂▂▂       2%          $12  │
▸ Other                                            4.2G (13%)            9%                │
```

The user chose this over "aligned columns only" and a "dashboard grid".

## Goals

1. **Aligned columns.** Each tree row's numbers move into fixed-width columns on the right,
   with a heading line above the tree, in this order:
   - **state:** a Claude pane's status and age: `wait 4m`, `work`, `idle 2h`. Empty for other
     rows.
   - **RAM:** size, share of the machine and the RAM graph, as today. The graph shows only where
     there is one today.
   - **CPU.**
   - **ctx:** a Claude pane's latest context, `140k`.
   - **month:** this month's spend, on project rows only, `$88`.

   Every row kind lines up: projects, panes, MCP lines, Docker lines, "outside WezTerm" and Other.
   Nothing on a row today is lost:
   - The left part keeps the ▲ marker, the agent dot, the label, the detail (title or
     command), the cwd, a working session's current tool and "(this deck)".
   - The left part is cut with `…` when the window is too narrow for it. The columns are never
     cut.
   - Red for a row over a threshold stays on its RAM and CPU.
   - A waiting row's state is in the waiting color.
2. **A side pane on the right**, when the window is wide enough:
   - **"Waiting on you"** leaves the tree and becomes a list in the pane:
     - oldest first, as today;
     - each wait takes two lines, `project · claude  age`, then the reason;
     - when nothing waits, the list reads "Nothing waiting on you" (dim).
   - **The machine line** (CPU, RAM, their graphs, the low-memory and swap warnings) moves under
     that list, wrapped to the pane's width.
   - **Keys:**
     - The pane is focusable, and **Tab / shift+Tab** move between the tree and the pane.
     - On a waiting row, every key that works on a waiting row in today's tree works the same:
       Enter jumps, `i` details, `c` checkpoint, `r` recap, `x` cleanup, `t` theme, `p` PM,
       `a` where it applies today.
     - ↑/↓ and j/k move within the pane.
     - The deck still starts with focus in the tree.
3. **In the wide mode, the `↳ reason` line under a waiting session in its project goes away.**
   The pane shows the reason.
4. **Narrow windows fall back to one column:**
   - Below a width threshold (my guess is about 120 columns; the builder can tune it against the
     real rows), the pane is hidden.
   - "Waiting on you" goes back to the top of the tree, the `↳ reason` lines come back, and the
     machine line goes back above the tree, as today.
   - The aligned columns stay in both modes.
   - Resizing switches modes live and keeps the cursor on the same row (by `node_id`, as
     rebuilds already do).

## Non-goals

- **The phone page (`web/page.html`):** it doesn't change. It already has its own waiting card
  and layout.
- **The other screens:** the PM view, history, detail, recap and Summaries don't change.
- **Sorting and hiding:** no sorting by column, and no hiding columns in config. Projects stay in
  config order.
- **Data:** nothing new is collected. Every number shown is already on `DeckSnapshot`, `Group`,
  `PaneRow` and `AgentView`.

## Anti-goals (failure even if it "works")

- **Losing something:** the tree loses folding, the cursor-follow on find, or a key that worked
  on a row before.
- **Numbers that don't line up:** a column wobbles between rows, for example because an
  emoji-width or wide character in a title shifts it. Use Rich's cell width, not `len`.
- **A pane too small to use:** on a narrow terminal, the side pane squeezes the tree until it
  can't be read.
- **Two places to act:** one wait is actionable from two places at once in the wide mode. The
  pane replaces the tree's waiting group; the two never show together.
- **A slower deck:** a poll becomes noticeably slower. The tree is already rebuilt on every
  change; laying out columns must not add more than a few ms for about 30 rows.

## Constraints

- **Stack:** Textual 8.2.8, and the tree stays a Textual `Tree` (folding, the cursor and
  `node_id`-based restore are built on it). `ui/` only; `core` doesn't change unless a pure
  helper belongs there (column formatting can live in `ui/widgets.py`).
- **Labels:** a `Tree` label is a single `Text` per line, indented by depth (`guide_depth`), so
  the columns are made by padding or cutting the left part to `available width − indent − columns
  width`. The deck must rebuild the labels on resize, since the widths depend on the widget's
  size.
- **Existing actions:** they read `self.deck_tree.cursor_node`. They must read the focused
  list's selected node instead (one helper), so the pane's rows work with them. The pane's rows
  should carry the same `Node` data as today's waiting rows (`Node(group, row, "pane")` of the
  waiting group), so `node_id`, `checkpoint`, `cleanup.place` and the rest need no change.
- **Filters:** `w` (waiting only) and `/` (find) still filter. In the wide mode they apply to the
  pane's list too, the way they apply to today's waiting group.
- **`w` in the wide mode (the user's answer):**
  - The tree keeps only the projects that have a waiting session, and shows only those sessions
    (each with its columns). The pane lists the same waits.
  - With nothing waiting, the tree shows today's "nothing waiting" label.
  - This needs a filter next to `core/filter.waiting_only`.
  - The narrow mode's `w` stays as it is today.
- **Text rendering:** markup off, as everywhere: titles and reasons are free text (`Text`, never
  markup strings).
- **Quality gate:** the usual one: ruff, pyright, pytest with ≥ 80% coverage, enola. Textual
  tests use `app.run_test(size=…)` to cover both modes. A headless screenshot at about 180 and 100
  columns is checked by eye before the PR, as for #121.

## Decided by the user

- Columns plus a side pane, the first option above.
- Columns: state, RAM with its share and graph, CPU, ctx, month. Nothing dropped.
- The side pane is focusable, Tab switches to it, and the keys work on its rows. "Waiting on you"
  moves out of the tree into it, and the machine numbers sit under it. Narrow windows fold back
  to one tree.
- The `↳ reason` line is dropped in the two-pane mode and kept in the narrow mode.

## My calls (the user can override)

- The heading line ("state  RAM  CPU  ctx  month") above the tree is dim, sits outside the tree
  so it doesn't scroll, and is aligned with the tree's columns.
- The side pane's width is a fixed ~42 columns, with a left border as the divider.
- The threshold is about 120 columns, so the tree keeps at least ~78 columns.
- A working session's current tool stays in the left part (it's text, not a number). The state
  column says only `work`.
- Ages in the state column use the deck's existing `age()` format.
- The pane's list is its own `Tree` (root hidden, flat), with no new widget kind, so selecting
  and `cursor_node` behave like the main tree.
- **The pane's two lines per wait** are two nodes: the wait (`Node(waiting group, row, "pane")`)
  and its reason (`role="reason"`). Both are cursor stops, and every key on either acts on that
  wait, as today's `↳` reason lines do. No skip logic.
- **The narrow mode's "Waiting on you" rows** keep today's inline `waiting_label`
  (project · claude, age, reason), cut with `…` to the tree's width, with no columns. They repeat
  their project's session, and their numbers sit on that project's row. The heading line still
  sits above the tree.
- **Focus when the mode switches:**
  - Narrow to wide: focus stays in the tree. A cursor on a waiting row (`w:` id) lands on that
    session's row in its project (`p:`), and if that isn't shown, on the project, as
    `_where_it_was` already does.
  - Wide to narrow with the pane focused: focus moves to the tree, and the cursor lands on the
    same wait in the restored "Waiting on you" group.

## Left to the builder

- Exact column widths, and how the left part is cut (cwd first, then detail).
- Whether column formatting is a new function next to `pane_label` / `group_label`, or a change to
  them. The narrow and wide modes share it.
- How the deck notices the resize: `on_resize` on the app or the tree, debounced if needed.
- The test layout, the headless screenshots, and how existing tests that assert the inline
  "RAM … CPU …" text are updated.
