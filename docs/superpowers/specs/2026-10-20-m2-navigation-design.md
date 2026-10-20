# M2: navigation — design

Issue: #5. Milestone M2 in `PLAN.md` §11. Builds on M1 (`2026-10-17-m1-read-only-deck-design.md`)
and the M0 focus findings in `PLAN.md` §4 and `AGENTS.md`.

## Outcome

From the deck, Enter or a click on a pane row lands you in that pane. Its tab is selected, and
its window is raised and focused, including when the pane is in another WezTerm window. `/`
filters the tree with a fuzzy match, and Enter jumps to the best match.

**Success criteria**
- Jumping to a pane in another window raises and focuses that window in under a second,
  once the Lua module is installed and required.
- Without the Lua module, a jump still selects the pane's tab and pane, and the deck says
  once how to enable raising.
- `/web` narrows the tree to matching panes as you type; Enter jumps to the best match; Esc
  restores the full tree.
- CI (ruff, pyright, pytest ≥ 80% branch coverage) is green.

Out of scope: themes (M3; they will live in the same Lua module), agent status (M4), the
browser front-end (M7, where focus is outside WezTerm and needs another raise path).

## How a jump works

1. `core.navigate.Navigator.jump(pane_id)` runs on a worker thread.
2. It calls `collectors.wezterm.activate_pane(pane_id)`:
   `wezterm cli --no-auto-start activate-pane --pane-id N`, with the socket from
   `find_socket` and a 3s timeout. This selects the tab and pane. It does not raise the
   window (measured in M0).
3. If the deck runs inside WezTerm (`WEZTERM_PANE` is set), it writes one OSC 1337 sequence
   to its own controlling terminal (`/dev/tty`), in a single `write()`:
   `ESC ] 1337 ; SetUserVar=cardak_focus=<base64("<pane_id>:<time_ns>")> BEL`.
   The nonce makes every request a change: WezTerm fires `user-var-changed` only when the
   value changes.
4. The Lua handler, registered by `require('cardak').apply(config)`, receives the event
   for the deck's pane and:
   - ignores any name other than `cardak_focus`;
   - parses the leading integer of the decoded value;
   - looks up `wezterm.mux.get_pane(id)` and returns if the pane is gone;
   - calls `pane:tab():activate()` and `pane:activate()`;
   - calls `gui_window():focus()` on `pane:tab():window()`, when that GUI window exists.
   Because the request comes from the focused WezTerm window, KWin lets the focus through
   (M0). WezTerm decodes the base64 before calling the handler.
5. The deck reports nothing when the jump succeeds. It shows a notification only for a
   failure, for "selected only", or for the one-time install hint.

The deck never writes into another pane's tty: bytes injected into another program's
output (claude, vim) could garble its rendering.

`Navigator.jump` returns a `JumpResult`:
- `kind`:
  - `"raised"` means the focus request was sent;
  - `"selected"` means the pane was activated but no raise was possible;
  - `"failed"`.
- `message: str | None`: shown when it is not None.
- `hint: bool`: true when the Lua module is missing and the hint has not been shown yet
  in this run.

## Components

```
src/cardak/
  cli.py                 + `cardak wezterm install [--dir PATH] [--force]`
  wezterm/               the Lua side; imports nothing else from cardak
    __init__.py
    cardak.lua           M.apply(config): registers the focus handler
    install.py           install(dest_dir, force) -> InstallResult; snippet(dest_dir) -> str
  collectors/wezterm.py  + activate_pane(pane_id, env=None, runner=subprocess.run) -> None
  core/
    navigate.py          Navigator(activate, write_tty, in_wezterm, lua_installed) and JumpResult
    filter.py            filter_snapshot(snap, query) -> FilterResult(snapshot, best_pane_id, n_matches)
  ui/app.py              jump, keys, and the search Input
```

Layering: `cli → {ui, wezterm} → core → collectors`. `core/filter.py` uses
`textual.fuzzy.Matcher`, the same matcher as Textual's command palette, and is core's only
Textual import. `wezterm/` is a leaf.

### collectors/wezterm.py: `activate_pane`

It discovers the socket and maps errors exactly as `list_panes` does, raising
`WeztermUnavailable(reason)`. A non-zero exit, such as a pane that no longer exists, becomes
`WeztermUnavailable` carrying the last stderr line.

### core/navigate.py

- `Navigator(activate: Callable[[int], None], write_tty: Callable[[bytes], None],
  in_wezterm: bool, lua_installed: Callable[[], bool])`. The defaults are built in
  `Navigator.live(env)`:
  - `activate` is `collectors.wezterm.activate_pane`;
  - `write_tty` opens `/dev/tty` in `wb` mode, writes and closes;
  - `in_wezterm` is `"WEZTERM_PANE" in env`;
  - `lua_installed` checks that `~/.config/wezterm/cardak.lua` exists, honouring
    `$XDG_CONFIG_HOME`.
- `focus_request(pane_id, nonce) -> bytes` is pure and builds the OSC sequence.
- `jump(pane_id)`:
  1. `activate(pane_id)`. A `WeztermUnavailable` becomes
     `failed("Couldn't jump: <reason>")`.
  2. If not in WezTerm: `selected("Selected pane N; the deck isn't running in WezTerm, so
     its window can't be raised")`.
  3. Otherwise it writes the focus request. An `OSError` becomes
     `selected("Selected pane N; couldn't ask WezTerm to raise it: <reason>")`.
  4. If `lua_installed()` is false, the result is `selected`, and the hint is set the first
     time only. Message: `Selected pane N. Run \`cardak wezterm install\` to also raise
     its window.` The request is still written: a module that is required but sits
     elsewhere still works.
  5. Otherwise `raised(None)`.

### core/filter.py

- `filter_snapshot(snap, query) -> FilterResult`:
  - a blank query returns the snapshot unchanged, `best_pane_id=None` and
    `n_matches=snap.n_panes`;
  - otherwise each pane is scored with `Matcher(query).match(haystack)`, where the
    haystack is `"<group name> <label> <detail> <cwd_display>"`. Panes scoring 0 are
    dropped;
  - groups with no remaining panes are dropped, and group order and pane order are kept;
  - group totals (CPU/RAM) are left as they are, since they describe the project, not the
    filter;
  - `best_pane_id` is the highest score, with ties going to the first in tree order;
  - `n_matches` is the number of panes kept;
  - an error snapshot (`snap.error`) passes through unchanged.

### wezterm/install.py and cardak.lua

- `cardak.lua` ships as package data, so it is included in the wheel and read through
  `importlib.resources`.
- `default_dir(env)` is `$XDG_CONFIG_HOME/wezterm`, else `~/.config/wezterm`.
- `install(dest_dir, force=False) -> InstallResult(path, status)`, where `status` is
  `"installed"`, `"updated"` or `"unchanged"`:
  - the write is atomic (temp file plus `os.replace`), with mode 0644, and the directory is
    created if missing;
  - an identical existing file means `unchanged`;
  - an existing file that differs and starts with cardak's marker line
    (`-- cardak: generated`) means `updated`;
  - an existing file without the marker raises `InstallError`, unless `force` is set.
- `snippet(dest_dir)` returns the text that `install` prints:
  - the one line to add, `require('cardak').apply(config)`, placed before `return config`;
  - a minimal complete `wezterm.lua` (`config_builder`, `apply`, `return config`) for
    people who have none;
  - the note that WezTerm reloads its config automatically.
- cardak never reads or edits `wezterm.lua`.

### ui/app.py

- **Tree selection:** Enter or a click fires `Tree.NodeSelected`.
  - On a pane row it calls `Navigator.jump` in a thread worker, and the result's message
    (if any) is shown via `notify`, escaped.
  - On a group row it toggles expand/collapse, as the Tree already does. The deck adds
    nothing there.
- **Keys:** `j`/`k` move the cursor down/up (arrows already work), and `/` focuses the
  search input.
  They are ordinary (non-priority) app bindings, so while the search `Input` has focus,
  `j`, `k` and `/` are typed into it.
- **Search `Input` (`#find`):**
  - hidden until `/`;
  - on each change, the current raw snapshot is re-filtered and the tree rebuilt;
  - on submit (Enter), it jumps to `best_pane_id` when there is one;
  - Esc clears the query, hides the input and refocuses the tree.
- **Filter state:** the app keeps the raw snapshot and the query. Every poll applies the
  filter before rendering. With an active query the header reads
  `cardak · N of M panes match "<query>"`. The cursor moves to the best match after each
  query change, but not on a poll refresh, where cursor restore works as in M1.
- **Collapsed groups** keep their state while filtering.

## Errors

| Situation | Behaviour |
|---|---|
| `activate-pane` fails (pane closed, WezTerm gone) | `Couldn't jump: <reason>`; the next poll removes a stale row |
| Deck not inside WezTerm | Pane selected; message says the window can't be raised |
| `/dev/tty` not writable | Pane selected; message gives the reason |
| `cardak.lua` not installed | Pane selected; install hint shown once per run |
| Module installed but not required in `wezterm.lua` | Pane selected without a raise; cannot be detected. `install`'s output and the hint both name the `require` line |
| `install` finds a foreign `cardak.lua` | `InstallError` and exit code 1, naming `--force` |
| Query matches nothing | Empty tree with `No pane matches "<query>"`; Enter does nothing |

## Testing

- `test_wezterm.py`: `activate_pane` builds the right command, passes the socket, and maps
  errors (reusing `FakeRunner`).
- `test_navigate.py`:
  - the exact bytes of `focus_request`, which round-trip through base64;
  - consecutive requests differ;
  - each `jump` branch, using fake `activate`/`write_tty`;
  - the hint appears only once;
  - `Navigator.live` defaults with a fake env.
- `test_filter.py`: a blank query, matching, dropped groups, unchanged group totals, the
  best match and its tie-break, an error snapshot passing through, and zero matches.
- `test_install.py`:
  - a fresh install, re-install unchanged, and update of an older cardak file;
  - a foreign file refused, and `--force` overwriting it;
  - mode 0644 and `XDG_CONFIG_HOME`;
  - the snippet text;
  - the packaged `cardak.lua` is readable via `importlib.resources`, carries the marker
    line, and registers `user-var-changed` for `cardak_focus`.
- `test_cli.py`: `cardak wezterm install --dir` prints the snippet and exits 0, and exits
  1 on a foreign file.
- `test_app.py` (pilot):
  - Enter on a pane calls a stub navigator, and Enter on a group toggles it;
  - a failure message is notified;
  - j/k move the cursor;
  - `/web` filters, Enter jumps to the best match, and Esc restores the tree;
  - the filter survives a poll refresh;
  - the header counts are right while filtering.
- **The Lua handler is not exercised in CI**, which has no WezTerm. The executor runs a
  live check: two WezTerm windows, a scratch `wezterm.lua` that requires the installed
  module, and a jump from the deck that brings the other window up, verified via
  `_NET_ACTIVE_WINDOW` as in M0, without stealing focus from other apps.

## Docs and release

- AGENTS.md:
  - `main` is protected: the `test` check is required on an up-to-date branch, changes go
    through PRs only, and the rules bind admins;
  - the `cardak wezterm install` flow;
  - the layering rule extended with `wezterm/`;
  - "jump" replaces the M0 focus notes as the place to look.
- PLAN.md: tick the M2 boxes.
- CHANGELOG, under Unreleased, Added: click-to-jump with window raise, `cardak wezterm install`
  and fuzzy find.
- Release `0.2.0` after merge (minor), with the same PR → green → tag flow.
