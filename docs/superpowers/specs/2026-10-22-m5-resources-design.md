# M5: resources — design

Issue #57 (includes #33). PLAN.md §11 M5, §12 risks.

## Why

A test run grew to well over a third of the machine's RAM while a `docker build` ran alongside,
and memory ran out several times within a few minutes. The kernel's OOM killer killed only the
test process, but systemd then stopped the whole terminal app scope it lived in
(`app-org.wezfurlong.wezterm-<id>.scope: Failed with result 'oom-kill'`), because the user
manager's `DefaultOOMPolicy=stop` applies to app scopes. Every pane and tab in that window died.
Nothing warned beforehand, and nothing afterwards said what had happened.

M5 makes the deck say, early and by name, which project and pane is eating the machine, so the
user can step in before the OOM killer does. It also keeps 30 days of per-project usage, so before
heavy work the user can see which projects hold resources worth freeing, and it leaves a plain-text
account of a crash that survives the crash.

## Goals

1. **Warn early, by name.** A desktop notification when memory gets tight or a project/pane gets
   too big or too busy, naming project, pane, command and numbers.
2. **Live numbers in the tree.** RAM, a short sparkline and CPU on every project and pane row,
   machine total at the top (#33).
3. **30-day history per project**, with a calendar strip and a per-project plot.
4. **Crash report.** When WezTerm dies, a text report of what led to it.
5. **Keep working with the deck closed.** Sampling, alerts, history and crash reports run in a
   daemon (systemd user service) outside WezTerm.

## Non-goals

- Monitoring terminals other than WezTerm, the only terminal cardak supports.
- Per-process history. History is per project (and Other); live numbers are per pane.
- Notifying on recovery.

## Anti-goals (a failure even if it works)

- **cardak kills, pauses, renices or limits any process, or jumps to / focuses a pane on an
  alert.** It only tells. The user decides what to do. (The read-only rule in AGENTS.md holds.)
- **Notification spam.** A build that legitimately runs at 800% for ten minutes must not produce
  ten notifications.
- **cardak changes systemd settings** (the `OOMPolicy` fix below) on its own. It tells the user.
- **The daemon itself becomes a noticeable load** or the first thing the OOM killer takes.

## Accounting

Every project's usage is the sum of:

- the process trees of all its WezTerm panes (what `collectors/processes.py` already computes per
  tty), **including** MCP servers under `claude`, which are shown as their own line inside the
  project (the existing `mcp_rss` / `mcp_cpu_pct` split) so they are not mistaken for the build;
- its **Docker containers**, matched to a project by the Compose label
  `com.docker.compose.project.working_dir`, or else by a bind-mount source inside the project's
  path;
- its **Docker builds**, matched when exactly one pane of that project is running a
  `docker build` / `docker buildx build` / `docker compose build` client. When several panes in
  different projects are building at once, the build usage is not split; it goes to Other and the
  alert text says "Docker builds, N panes".

**Other is the remainder:** machine used − Σ projects. It covers the desktop, browsers, WezTerm
panes not in any project, and unmatched Docker work. Projects + Other = machine total, always.

In the tree, the existing "Other" group (unassigned panes) carries the full remainder on its row;
its pane children show their own numbers, and one extra child line shows "outside WezTerm" with
the rest. *(My call.)*

Docker is read-only: list containers and their labels/mounts through the Docker Engine (the user is
in the `docker` group; without access, Docker usage falls into Other), read per-container usage from cgroup v2 files
(`/sys/fs/cgroup/system.slice/docker-<id>.scope/memory.current`, `cpu.stat`; systemd cgroup
driver). BuildKit build steps have been seen in cgroups named like
`system.slice:docker:<id>` in kernel OOM reports — **the builder must spike this and
record fixtures** before relying on it. If Docker is not installed or not reachable, Docker usage
silently falls into Other.

CPU is reported the way the deck already does it: percent where 100% = one core.

**What "RAM used" means** *(my call)*: machine used = `MemTotal − MemAvailable`. Pane and project
RAM is their processes' memory (RSS today; PSS from `/proc/<pid>/smaps_rollup` if the builder
measures it within the daemon's budget, since RSS double-counts shared pages), Docker is
`memory.current − inactive_file` (as `docker stats` counts it). Because these measures overlap, the invariant is approximate: Other is clamped
at 0, and the tree never shows a negative number.

## Alerts

All thresholds are absolute numbers in config (see Config). Defaults for an example
16-core, 32 GB machine in parentheses.

| Alert | Fires when | Level |
|---|---|---|
| Memory tight | available RAM < 15% of total (4.8 GB) for 10 s | warning |
| Memory critical | available RAM < 7% (2.2 GB), or swap full | critical |
| Big pane/project | one pane's or project's RAM > 25% of total (8 GB) | warning |
| Fast growth | a pane's or project's RAM grows > 1 GB/min, sustained 2 min | warning |
| Busy pane/project | one pane's or project's CPU > half the cores (800%) for 2 min | warning |
| Busy machine | total CPU > 90% for 2 min | warning |

- Critical uses `notify-send -u critical` (stays until dismissed) and a sound; warnings use normal
  urgency and, *my call*, the same sound setting as M4 waiting pings (`[notify] sound`).
- **Every alert names the culprit**: the biggest piece of the biggest project, e.g.
  `api-gw · pytest in pane 12 · 13.9 GB of the project's 15.1 GB, +1.4 GB/min · RAM 88% used`.
  Machine-level alerts name the WezTerm pane using the most; if most usage is outside projects,
  the text says "most of it is outside WezTerm" (or "Docker builds, N panes").
- **Quiet rules:** at most one notification per culprit (pane or project) per alert kind per
  10 minutes; a warning → critical escalation notifies immediately; nothing on recovery. Every
  alert, including suppressed repeats, is written to the event log.
- Only the daemon alerts. The deck never notifies about resources, so nothing doubles.
- **Who can be a culprit** *(my call)*: every WezTerm pane, including panes in no project (they
  are in the dev space), and every project. **Other as a whole never gets big/growth/busy
  alerts**: it holds the desktop and browsers and would fire all day; it only feeds the
  machine-level alerts. When a pane pushes its project over a threshold, that is **one**
  notification: the quiet rule is keyed on the project (or the pane, for a pane in no project)
  per alert kind, and the text names the pane.

## Crash report

The daemon samples every ~2 s, so it knows each pane's usage right up to a crash. When the
WezTerm GUI process disappears (its `gui-sock-<pid>` goes stale / the pid is gone), the daemon:

1. reads the kernel/systemd journal around that moment (readable unprivileged for members of `adm` or
   `systemd-journal`; without it, the report falls back to the samples alone) for `oom-kill:` lines (killed task name and pid, `task_memcg` showing the
   WezTerm scope) and the scope's `Failed with result 'oom-kill'`;
2. joins the killed pid to its pane/project from the last samples, and takes that pane's RAM
   over the preceding minutes;
3. writes a text report to the event log, sends it as a notification, and the deck shows it at
   the top on next start until dismissed.

Example:

> WezTerm was closed at 15:28:48 on Oct 21. The machine ran out of memory (32 GB; swap full).
> The kernel killed `pytest` (pid 41872) in api-gw, pane 12, which grew from 2.1 GB to 13.9 GB
> in 6 minutes. systemd then stopped the whole WezTerm window, because its policy stops an app
> when one of its processes is killed (`OOMPolicy=stop`). The 5 biggest users then: …

**Only OOM-caused exits are loud** *(my call)*: a notification and the deck banner happen only when
the journal shows an OOM kill in that WezTerm scope or the scope `Failed with result 'oom-kill'`
(wait a few seconds for the journal to catch up before deciding). Any other exit, such as a normal
quit or restart, is a single quiet event-log line with the last samples, no notification and no
banner. Each WezTerm GUI process is tracked on its own: one dying while others stay up is judged
the same way. If the WezTerm scope's `OOMPolicy` is still `stop`, the report ends with the
one-line fix.

**The OOMPolicy fix** (told to the user, never applied by cardak): a systemd user drop-in that sets
`OOMPolicy=continue` for WezTerm's app scopes, so only the culprit dies. Expected form:
`~/.config/systemd/user/app-org.wezfurlong.wezterm-.scope.d/oom.conf` with `[Scope]`
`OOMPolicy=continue` (prefix drop-in). **The builder must verify** that a prefix drop-in applies
to such scopes before cardak prints this advice, without touching the user's WezTerm or
`~/.config` *(my call on how)*: put the drop-in in the runtime unit dir
(`$XDG_RUNTIME_DIR/systemd/user/`, gone at logout), start a throwaway scope with the same prefix
(`systemd-run --user --scope --unit=app-org.wezfurlong.wezterm-cardaktest.scope -p MemoryMax=…`),
OOM one child inside it, and check the scope survives; then remove the drop-in. If the prefix form
does not work, the advice becomes `DefaultOOMPolicy=continue` in
`~/.config/systemd/user.conf.d/`, stated plainly as affecting every app the user launches.
`cardak resources install` checks the running WezTerm scope's `OOMPolicy` and prints the advice
when it is `stop`.

## Event log

Stored in `cardak.db`, kept 30 days. Entries: resource alerts (including suppressed repeats),
crash reports, daemon start/stop. Viewed in the deck (key left to the builder) as a newest-first
list of plain text lines; crash reports expand to their full text.

## History

- One sample per minute per project and for Other: RAM (mean and max over the minute) and CPU
  (mean and max), stored in `cardak.db`, pruned at 30 days. ~1 M rows worst case; fine. *(My call:
  existing DB, not a new file.)*
- A project with no live panes and no matched Docker work writes no rows; a day without rows has
  no data.

### History view (opened from the deck with `h`; key is the builder's call if taken)

```
history · last 30 days · RAM                                  [c] CPU  [s] sort
project      now   median    peak  alerts  Sep 23        Oct 7           Oct 22
api-gw    15.1 GB  4.2 GB 17.3 GB     5    ██ ██▓██▓██▓▓██▓██  ██▓██▒▓█▓
cardak     2.1 GB  1.8 GB  3.0 GB     0    ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
Other      9.8 GB  7.0 GB 14.6 GB     -    ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
peak RAM per day:  ░ <2 GB  ▒ 2–5  ▓ 5–10  ▓ 10–15  █ 15+
```

- Columns: now, **median** (over the minutes the project was running, so idle days don't pull it
  to zero), peak, alert count, then a 30-cell calendar strip (one cell per day = that day's peak).
- **A day with no data is an empty cell**, not a "zero" colour.
- Colour legend at the bottom. *My call:* band edges derive from the machine's RAM and line up with
  the alert thresholds (top band = above the big-pane threshold), so "red" means "would have
  alerted".
- `c` toggles the whole view to CPU; `s` cycles sort (peak by default).

### Day click → plot

Clicking (or Enter on) a project's day cell opens that project's **30-day line plot** of RAM (or
CPU, matching the view) with the alert threshold drawn as a line and the selected day marked;
←/→ moves the selected day; days without data are gaps in the line, not zero. Below the plot: the
day's peak and its alerts from the event log. `esc` returns. How to draw the plot (a Textual
plotting widget such as `textual-plotext`, or hand-drawn braille) is the builder's call; a new
dependency is fine if it stays out of `cardak hook`'s import path.

## Live view (tree)

```
cardak   CPU 38% ▂▃▃▅▄▃   RAM 27.6 / 32 GB ▃▄▅▆▇█  ⚠ 14% free
▾ api-gw                        15.1 GB ▂▃▅▇█  210%
    ● claude · fix tests          1.3 GB         12%
      └ MCP: playwright           0.4 GB
    ▲ pane 12 · pytest           13.9 GB ▁▂▄▆█  180%  +1.4 GB/min
▾ cardak                         2.1 GB ▃▃▃▃▃   15%
      🐳 docker: buildkit         0.9 GB          5%
▸ Other                          9.8 GB ▅▅▅▆▅   61%
```

- Header: machine CPU and RAM with short sparklines; a warning marker when memory is tight.
- Every project and pane row: RAM, sparkline (last few minutes), CPU. MCP and Docker as child
  lines. A row that is currently over a threshold is marked (▲) and coloured.
- The deck samples processes itself every refresh as today, so live numbers work without the
  daemon. Header sparklines and project history come from the daemon when it runs; without it,
  the deck shows a one-line hint (`cardak resources install`) and keeps only in-memory sparklines.

## Daemon and install

- `cardak daemon`: long-running, samples every ~2 s, writes minute rollups, runs alerts, watches
  for WezTerm exit, prunes. Talks to WezTerm through `collectors/wezterm.py`'s existing
  `gui-sock-<pid>` discovery, re-resolving after failures, always `--no-auto-start` (PLAN §12).
  When no WezTerm GUI is running it keeps sampling the machine (Other) and waits.
- Runs as systemd user service `cardak.service` (its own unit, so a WezTerm scope stop doesn't
  take it down). `Restart=on-failure`. Lightweight: no Textual import; target well under 1% of one
  core and tens of MB.
- **`cardak resources install`**: after asking (or `--yes`), writes
  `~/.config/systemd/user/cardak.service` (marker line, same overwrite rules as other generated
  files), runs `systemctl --user daemon-reload` and `enable --now`; writes the `[resources]`
  defaults (below) if the section is absent; checks `OOMPolicy` and prints the fix if needed.
  `cardak resources uninstall` disables and removes the unit only.
- Add the unit to the "Files cardak writes" table in AGENTS.md.

## Config

`[resources]` in `~/.config/cardak/projects.toml`, written by `cardak resources install` from the
machine's cores and RAM, each value with a comment saying how it was derived:

```toml
[resources]
ram_warn_available_gb = 4.8      # 15% of 32 GB
ram_critical_available_gb = 2.2  # 7% of 32 GB
pane_ram_gb = 8.0                # 25% of 32 GB
growth_gb_per_min = 1.0
pane_cpu_pct = 800               # half of 16 cores
machine_cpu_pct = 90
sustain_s = 120
repeat_min = 10
```

- Written with `tomlkit` so comments survive. Re-running install never overwrites existing values;
  `cardak resources install --refresh-defaults` recomputes them (e.g. after a RAM upgrade).
- A missing section or key falls back to the same formulas at runtime.
- Key names are the builder's call; the meaning above is fixed.

## Constraints

- Layering per AGENTS.md: new collectors (Docker; journal reading for OOM events; cgroup reads;
  machine totals via psutil) live in `collectors/`, each external format behind one module.
  Accounting, thresholds, alert decisions and crash-report text are pure `core/` functions tested
  without the machine.
- Record fixtures first: `docker` container JSON and a BuildKit cgroup listing; a kernel
  journal `oom-kill` sequence (sanitized); `/proc/meminfo`.
- `cardak hook`'s budget and import guards are untouched; the daemon is a separate command.
- Coverage floor stays at 80%.
- No writes under `~/.config` or `~/.config/systemd` except through `cardak resources install`
  after asking.
- Live checks never touch the user's running WezTerm or real state dir (AGENTS.md isolation rules);
  the daemon under test runs with scratch `XDG_*` dirs and is not installed as the user's service.

## Delivery

Three PRs, each its own minor release, each with its own plan, in this order:

1. **Live numbers** (0.6.0): tree columns, sparklines kept in memory, machine header (#33), MCP
   and Docker child lines, accounting and Other. No daemon. The Docker/BuildKit spike and fixtures
   happen here.
2. **Daemon and alerts** (0.7.0): `cardak daemon`, `cardak resources install/uninstall`,
   `[resources]` defaults, all alerts and quiet rules, the OOMPolicy check and advice (after
   the verification above).
3. **History and crash report** (0.8.0): minute rollups, history view, day plot, event log view,
   crash report. M5 in PLAN.md is ticked off when this one ships.

## Left to the builder

Module split, schema, sampling details, plot widget, key bindings other than `h`/`c`/`s`, how the
deck learns daemon liveness, BuildKit attribution mechanics (after the spike), and whether the
fast-growth window needs smoothing.
