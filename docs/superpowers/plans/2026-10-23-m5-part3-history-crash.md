# M5 part 3: history, event log and crash report — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The daemon keeps 30 days of per-project minute rollups and writes a crash report when a WezTerm GUI dies. The deck gains a history view (`h`), a day plot, an event log (`e`) and a crash banner. This ships as 0.8.0 and completes M5.

**Architecture:**
- **Data:** the daemon gains two pure helpers, `core/history.Rollup` (minute accumulator) and `core/crash.CrashWatch` (tracks GUI pids, recent pane RSS and the pid → pane map). A new `collectors/journal.py` reads `journalctl -o json`. `core/crash.report` turns the journal and the last samples into text. Everything is stored in `cardak.db`, schema 4.
- **Deck:** it reads that data through a second `Store` connection, `log`. Three new screens: `ui/history.py`, `ui/plot.py` (hand-drawn braille, no new dependency) and `ui/events.py`.

**Tech Stack:** Python 3.13, Textual (DataTable, Tree, Static), SQLite, `journalctl` (systemd 255).

**Spec:** `docs/superpowers/specs/2026-10-22-m5-resources-design.md` (sections Crash report, Event log, History, Day click → plot, Delivery 3).

## Global Constraints

- Layering: `cli → {ui, wezterm, claude, llm, systemd} → core → collectors`.
  - `core` never imports `cardak.systemd`, and `collectors` import nothing from cardak.
  - The journal's format lives only in `collectors/journal.py`. GUI pid and scope discovery lives in `collectors/wezterm.py`.
- `cardak hook` budget and import guards are untouched. `core/store.py` dataclasses are primitives only, because the hook imports store.
- Read-only: cardak never kills, renices or focuses anything on a crash, and never writes the OOMPolicy drop-in. The report only tells.
- "Only OOM-caused exits are loud." A notification and the deck banner happen only when the journal shows an OOM kill in that WezTerm scope, or `<scope>: Failed with result 'oom-kill'`. Any other exit is one quiet event-log line.
- Wait `CHECK_AFTER_S = 5.0` after a GUI disappears before reading the journal.
- History: one row per minute per project and for Other (RAM mean/max, CPU mean/max), pruned at 30 days. A day without rows is an empty cell, and a gap in the plot.
- History columns: now, median (over the minutes with rows), peak, alerts, then 30 day cells (that day's peak). `c` toggles RAM/CPU, `s` cycles sort (peak first), Enter or click on a day cell opens the plot, and ←/→ and `esc` work in the plot.
- Coverage floor 80%. ruff, ruff format, pyright clean. Fixtures sanitized: `/home/user/...`, projects `webshop` / `api-gw`, no hostnames, made-up pids and unit ids.
- Live checks: scratch `XDG_STATE_HOME` / `XDG_CONFIG_HOME`, `[notify] desktop = false`, `sound = "off"`. Never touch the user's WezTerm, its state dir or `~/.config`, and never install the service.
- Commits: conventional, ending `(#57)`, with the session trailers.

## Review Focus

1. **A v3 `cardak.db` with alerts in it upgrades to v4 and keeps them.** Pinned by `test_v3_db_upgrades_and_keeps_events` (Task 3).
2. **A fresh install with no history rows yet.** The history view shows live projects with empty cells and a hint, never an error. Pinned by `test_history_rows_live_only` (Task 4) and `test_history_screen_empty_hint` (Task 8).
3. **`journalctl` missing, failing or unreadable for the kernel.** The daemon keeps running, and the report falls back to the samples. It is loud only if memory was critical just before. Pinned by `test_read_journal_unreadable` (Task 1) and `test_report_without_journal_*` (Task 5).
4. **Two WezTerm GUIs, one dies.** Only that one is reported, judged by its own scope. Pinned by `test_one_of_two_guis_dies` (Task 5).
5. **Local-day bucketing and the daemon stopping mid-minute.** Days are local calendar days, and the partial minute is written on stop. Pinned by `test_days_are_local` (Task 3) and `test_stop_flushes_partial_minute` (Task 6).

---

### Task 1: Journal fixtures and `collectors/journal.py`

**Files:**
- Create: `tests/fixtures/journal/kernel.jsonl`, `tests/fixtures/journal/user.jsonl`, `src/cardak/collectors/journal.py`
- Test: `tests/test_journal.py`

**Interfaces:**
- Produces:
  - `JournalEntry(ts: float, message: str)` (frozen).
  - `OomKill(ts: float, pid: int, task: str, memcg: str, anon_rss: int | None)` (frozen).
  - `parse_entries(text: str) -> list[JournalEntry]`.
  - `oom_kills(entries: Sequence[JournalEntry]) -> list[OomKill]`.
  - `failed_scopes(entries: Sequence[JournalEntry]) -> set[str]`.
  - `read_journal(since: float, until: float, runner: Runner = subprocess.run) -> tuple[list[JournalEntry] | None, list[JournalEntry]]`. Returns `(kernel, user)`. `kernel` is `None` when unreadable.

- [ ] **Step 1: Record the fixtures** (one `journalctl -o json` object per line, keeping only `__REALTIME_TIMESTAMP`, `_TRANSPORT`, `MESSAGE`).

  The kernel fixture has 4 lines, all `_TRANSPORT: "kernel"`:
  - ts `1792589326370100`: `postgres invoked oom-killer: gfp_mask=0x140cca(GFP_HIGHUSER_MOVABLE|__GFP_COMP), order=0, oom_score_adj=0`
  - ts `1792589326370445`: `oom-kill:constraint=CONSTRAINT_NONE,nodemask=(null),cpuset=docker-aaaa….scope,mems_allowed=0,global_oom,task_memcg=/user.slice/user-1000.slice/user@1000.service/app.slice/app-org.wezfurlong.wezterm-0123456789abcdef0123456789abcdef.scope,task=pytest,pid=41872,uid=1000`. The cpuset is 64 `a`s.
  - ts `1792589326370500`: `Out of memory: Killed process 41872 (pytest) total-vm:14577636kB, anon-rss:14230296kB, file-rss:8kB, shmem-rss:0kB, UID:1000 pgtables:28308kB oom_score_adj:200`
  - ts `1792589014000000`: a second `oom-kill:` with `task_memcg=…/app.slice/app-org.kde.konsole-fedcba9876543210fedcba9876543210.scope,task=python,pid=40001,uid=1000`, and no Killed line.

  The user fixture has 4 lines, all `_TRANSPORT: "journal"`:
  - `app-org.wezfurlong.wezterm-0123456789abcdef0123456789abcdef.scope: A process of this unit has been killed by the OOM killer.`
  - the same scope: `Failed with result 'oom-kill'.`
  - the same scope: `Consumed 8h 44min 37.147s CPU time.`
  - `Started app-org.wezfurlong.wezterm-89abcdef0123456789abcdef01234567.scope - WezTerm.`

  Add one non-JSON line and one line whose `MESSAGE` is a list of ints (journald's form for non-UTF-8) to `user.jsonl`. `parse_entries` must skip both.

- [ ] **Step 2: Write the failing tests**

```python
def test_parse_entries_skips_bad_lines() -> None:
    entries = parse_entries((FIX / "user.jsonl").read_text())
    assert len(entries) == 4 and entries[0].ts == pytest.approx(1792589326.0, abs=400)

def test_oom_kills_from_kernel_fixture() -> None:
    kills = oom_kills(parse_entries((FIX / "kernel.jsonl").read_text()))
    assert [(k.pid, k.task, k.anon_rss) for k in kills] == [(41872, "pytest", 14230296 * 1024), (40001, "python", None)]
    assert kills[0].memcg.endswith("app-org.wezfurlong.wezterm-0123456789abcdef0123456789abcdef.scope")
    assert kills[0].ts == 1792589326.370445

def test_failed_scopes() -> None:
    assert failed_scopes(parse_entries((FIX / "user.jsonl").read_text())) == {
        "app-org.wezfurlong.wezterm-0123456789abcdef0123456789abcdef.scope"}

def test_read_journal_calls(): ...
    # FakeRun records argv; asserts two calls:
    # ["journalctl", "-o", "json", "--no-pager", "--since", "@1000", "--until", "@1060", "_TRANSPORT=kernel"]
    # ["journalctl", "--user", "-o", "json", "--no-pager", "--since", "@1000", "--until", "@1060"]

def test_read_journal_unreadable() -> None:
    # kernel call: rc 0, stdout "", stderr containing "not seeing messages from other users" -> kernel None
    # FileNotFoundError from the runner -> (None, [])
    # rc 1 on the user call -> user []
```

- [ ] **Step 3: Run them and confirm they fail**
  - Run: `uv run pytest tests/test_journal.py -q`
  - Expected: FAIL (`ModuleNotFoundError: cardak.collectors.journal`).

- [ ] **Step 4: Implement `collectors/journal.py`.** The module docstring says it is the only reader of the journal's format.
  - `oom_kills`: split the text after `oom-kill:` on `,`, then each token on its first `=`, ignoring tokens without one (`global_oom`). Take `anon_rss` from the `Killed process <pid> (` line with the same pid (kB × 1024).
  - `failed_scopes`: regex `^(\S+\.scope): Failed with result 'oom-kill'`.
  - Timestamps are `int(__REALTIME_TIMESTAMP) / 1e6`.
  - `timeout=5`. Any `OSError` or `TimeoutExpired` → `(None, [])`.

- [ ] **Step 5: Confirm the tests pass.**
  - Run: `uv run pytest tests/test_journal.py -q`
  - Expected: PASS.

- [ ] **Step 6: Commit** `feat(collectors): read OOM kills and failed scopes from the journal (#57)`.

### Task 2: GUI processes and pane pids

**Files:**
- Modify: `src/cardak/collectors/wezterm.py`, `src/cardak/collectors/processes.py` (`PaneProcs`, `_pane_procs`), `src/cardak/core/snapshot.py` (`PaneRow`, `_row`; `DeckSnapshot.thresholds`), `src/cardak/core/source.py` (`_resources` sets `thresholds`)
- Test: `tests/test_wezterm.py`, `tests/test_processes.py`, `tests/test_snapshot.py`, `tests/test_source.py`

**Interfaces:**
- Produces:
  - `wezterm.gui_processes(env: Mapping[str, str] | None = None, proc: Path = Path("/proc")) -> dict[int, str | None]`. Maps the pid of every live `gui-sock-<pid>` to its systemd unit: the last path component of `/proc/<pid>/cgroup`'s `0::` line when it ends in `.scope`, else `None`.
  - `PaneProcs.pids: frozenset[int] = frozenset()`. It holds work and MCP pids.
  - `PaneRow.pids: frozenset[int] = frozenset()`.
  - `DeckSnapshot.thresholds: Thresholds | None = None`. `LiveSource._resources` sets it whenever a machine sample exists.

- [ ] **Step 1: Write the failing tests.**
  - `test_gui_processes_maps_scopes(tmp_path)`:
    - Setup: a fake runtime dir with `gui-sock-<os.getpid()>`, a dead `gui-sock-999999999` and `sock`, plus a fake `proc/<pid>/cgroup` containing `0::/user.slice/…/app.slice/app-org.wezfurlong.wezterm-0123….scope\n`.
    - Expected: `{os.getpid(): "app-org.wezfurlong.wezterm-0123….scope"}`.
    - With the cgroup file missing: `{pid: None}`.
  - `test_pane_procs_lists_pids`: pids of work and MCP processes are both in `pids`.
  - `test_row_carries_pids` in test_snapshot.
  - `test_resources_sets_thresholds` in test_source: `snap.thresholds == derive(...)`.
- [ ] **Step 2: Run them and confirm they fail.**
  - Run: `uv run pytest tests/test_wezterm.py tests/test_processes.py tests/test_snapshot.py tests/test_source.py -q`
  - Expected: the 4 new tests FAIL.
- [ ] **Step 3: Implement.**
  - Share the live-socket glob between `find_socket` and `gui_processes`, as a private `_live_gui_socks(directory)`.
  - `pids` is excluded from nothing: snapshot equality now changes when the process set does, and that is accepted.
- [ ] **Step 4: Confirm the full suite passes.**
  - Run: `uv run pytest -q`
  - Expected: PASS.
- [ ] **Step 5: Commit** `feat: GUI scopes and pane pids for the crash report (#57)`.

### Task 3: Store schema 4

**Files:**
- Modify: `src/cardak/core/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Produces, in `store.py` (primitives only):
  - `MinuteRow(minute: int, subject: str, name: str, ram_mean: int, ram_max: int, cpu_mean: float, cpu_max: float)`.
    - `minute = int(ts // 60)`.
    - `subject` is `"project:<path>"` or `"other"`.
  - `DayPeak(subject: str, name: str, day: str, ram_peak: int, cpu_peak: float)`. `day` is the local `YYYY-MM-DD`, and `name` is the name from the newest row.
  - `SCHEMA_VERSION = 4`.
  - New tables:
    - `resource_minutes(minute INTEGER, subject TEXT, name TEXT, ram_mean INTEGER, ram_max INTEGER, cpu_mean REAL, cpu_max REAL, PRIMARY KEY (subject, minute))` plus index on `minute`.
    - `dismissed_reports(ts REAL PRIMARY KEY)`.
  - `Store` methods:
    - `record_minutes(rows: Sequence[MinuteRow]) -> None`: INSERT OR REPLACE, one transaction.
    - `history_days(since: float) -> list[DayPeak]`.
    - `history_medians(since: float) -> dict[str, tuple[float, float]]`: subject → (median ram_mean, median cpu_mean).
    - `history_hours(subject: str, since: float) -> list[tuple[int, int, float]]`: (hour start ts, ram max, cpu max), oldest first.
    - `alert_counts(since: float) -> dict[str, int]`: notified events per subject, kinds memory/big/growth/busy/machine_busy.
    - `latest_crash(since: float) -> ResourceEvent | None`: the newest `kind == "crash"` and `level == "critical"` not in `dismissed_reports`.
    - `dismiss_report(ts: float) -> None`.
    - `prune`: also deletes minutes older than `RESOURCE_KEEP_SECONDS`, and dismissals older than that.
  - `ResourceEvent.kind` gains `"crash"` and `"daemon"`. `level` gains `"info"`. Update the comments.
  - Every method wraps `sqlite3.Error` in `StoreError`, as the existing ones do.

- [ ] **Step 1: Write the failing tests.**
  - `test_minutes_roundtrip_days_and_medians`: rows over 2 days →
    - `history_days` gives the per-day max;
    - medians are the middle value of the ram_mean values (odd count) and the lower-middle for an even count;
    - re-recording the same (subject, minute) replaces it.
  - `test_days_are_local(monkeypatch)`:
    - Setup: `TZ=Europe/Berlin`, `time.tzset()`, then a row at 23:30 UTC on Oct 21.
    - Expected: `day == "2026-10-22"`.
    - Reset TZ in a finalizer.
  - `test_history_hours_buckets_by_hour`.
  - `test_alert_counts_counts_notified_only`.
  - `test_latest_crash_skips_dismissed_and_quiet`: a critical crash, then an info crash, then dismissing the critical one → `None`.
  - `test_v3_db_upgrades_and_keeps_events`: build a v3 DB with the v3 schema text inlined in the test and one `resource_events` row, open it with `Store` → `user_version == 4`, the row is still there, and `record_minutes` works.
  - `test_prune_drops_old_minutes`.
- [ ] **Step 2: Run them and confirm they fail.**
  - Run: `uv run pytest tests/test_store.py -q`
  - Expected: the new tests FAIL.
- [ ] **Step 3: Implement.**
  - Days come from SQL `date(minute*60, 'unixepoch', 'localtime')`.
  - Medians: per subject, `ORDER BY ram_mean LIMIT 1 OFFSET (n-1)/2`, and the same for CPU.
  - Budget: on 500k rows over 20 subjects, `history_days` + `history_medians` take < 300 ms. Measure in Task 9.
- [ ] **Step 4: Confirm the full suite passes.**
  - Run: `uv run pytest -q`
  - Expected: PASS. Fix any test that pinned `SCHEMA_VERSION == 3`.
- [ ] **Step 5: Commit** `feat(store): minute rollups and crash dismissals, schema 4 (#57)`.

### Task 4: `core/history.py`: the rollup and the view model

**Files:**
- Create: `src/cardak/core/history.py`
- Test: `tests/test_history.py`

**Interfaces:**
- Consumes: `MinuteRow`, `DayPeak` (Task 3), `Thresholds`, `DeckSnapshot`.
- Produces:
  - `class Rollup`:
    - `add(snap: DeckSnapshot, now: float) -> list[MinuteRow]`. It accumulates every group of kind config/git (subject `project:<key>`) and other (subject `other`, name `Other`). When `int(now // 60)` changes, it returns the finished minute's rows.
    - `flush() -> list[MinuteRow]`: returns the partial minute and clears it.
    - A `None` cpu is left out of the CPU mean. All-`None` gives 0.0.
    - Snapshots without a machine sample are ignored.
  - `Metric = Literal["ram", "cpu"]`; `SORTS = ("peak", "now", "median", "alerts", "name")`; `DAYS = 30`; `CELLS = "░▒▓▓█"`.
  - `HistoryRow(subject: str, name: str, now: float | None, median: float | None, peak: float | None, alerts: int | None, days: tuple[float | None, ...])`.
    - `days` is oldest first, length 30, and the last cell is today.
    - `alerts` is `None` for Other.
  - `history_rows(days: Sequence[DayPeak], medians: Mapping[str, tuple[float, float]], counts: Mapping[str, int], live: DeckSnapshot | None, today: date, metric: Metric) -> list[HistoryRow]`. Subjects are those with history plus those live now. `name` is the live name if live, else the stored one.
  - `sort_rows(rows: Sequence[HistoryRow], key: str) -> list[HistoryRow]`. Descending for numbers, with `None` last. `name` sorts ascending. Other is always last.
  - `edges(t: Thresholds, metric: Metric) -> tuple[float, float, float, float]`. Top = `t.pane_ram` or `t.pane_cpu_pct`, and the edges are top × (¼, ½, ¾, 1), so █ means "would have alerted".
  - `band(value: float | None, e) -> int`: −1 for `None`, else 0–4.
  - `legend(e, metric) -> str`, for example `"peak RAM per day:  ░ <2.0 GB  ▒ 2.0–4.0  ▓ 4.0–6.0  ▓ 6.0–8.0  █ 8.0+"` (32 GB example). The CPU form is `"peak CPU per day:  ░ <200%  ▒ 200–400  …"`.

- [ ] **Step 1: Write the failing tests.**
  - `test_rollup_emits_on_minute_change`: 3 snapshots at t = 0, 2, 61 → rows for minute 0 with mean/max of the first two. Other is included.
  - `test_rollup_ignores_snap_without_machine`.
  - `test_history_rows_fill_days`: a gap day is `None`, and today is the last cell.
  - `test_history_rows_live_only`: no DayPeaks, one live project → a row with all-`None` days, `now` set, median and peak `None`.
  - `test_sort_keeps_other_last`.
  - `test_edges_and_legend` with `derive(ResourceSettings(), 32 * GIB, 16)`: the edges are 2, 4, 6, 8 GiB, plus the legend string exactly as above.
- [ ] **Step 2: Run them and confirm they fail.**
  - Run: `uv run pytest tests/test_history.py -q`
  - Expected: FAIL (module missing).
- [ ] **Step 3: Implement** (pure, no I/O).
- [ ] **Step 4: Confirm they pass.**
  - Run: `uv run pytest tests/test_history.py -q`
  - Expected: PASS.
- [ ] **Step 5: Commit** `feat(core): minute rollups and the history view model (#57)`.

### Task 5: `core/crash.py`: the watch and the report

**Files:**
- Create: `src/cardak/core/crash.py`
- Test: `tests/test_crash.py`

**Interfaces:**
- Consumes:
  - `JournalEntry`, `OomKill`, `oom_kills` and `failed_scopes` (Task 1).
  - `PaneRow.pids` (Task 2).
  - `Thresholds`, `memory_level` and `watch.gb`.
- Produces:
  - Constants: `CHECK_AFTER_S = 5.0`, `JOURNAL_BEFORE_S = 120.0`, `KEEP_S = 600.0`, `PID_KEEP_S = 60.0`.
  - `Gone(pid: int, scope: str | None, ts: float)`.
  - `CrashReport(loud: bool, title: str, text: str)`.
  - `class CrashWatch`:
    - `observe(guis: Mapping[int, str | None], snap: DeckSnapshot, now: float) -> None`:
      - keeps each pane's RSS series for `KEEP_S`;
      - keeps the pid → (pane_id, time last seen) map, dropping pids not seen for `PID_KEEP_S`;
      - keeps the last snapshot without `error` and its machine sample;
      - a GUI pid present before and missing now becomes pending `Gone(pid, its last-known scope, now)`.
    - `due(now: float) -> list[Gone]`: removes and returns the pending entries older than `CHECK_AFTER_S`.
    - `report(gone: Gone, kernel: Sequence[JournalEntry] | None, user: Sequence[JournalEntry], t: Thresholds | None) -> CrashReport`.
  - Relevant kills:
    - `OomKill`s with `gone.ts − JOURNAL_BEFORE_S ≤ ts ≤ gone.ts + 10`;
    - whose memcg ends with `/` + scope, or contains `app-org.wezfurlong.wezterm-` when the scope is unknown.
  - Loud when either:
    - a relevant kill exists, or `gone.scope ∈ failed_scopes(user)`; or
    - the kernel journal is `None` and the last machine sample within 30 s was at the `critical` memory level.
  - Text: sentences joined by a space, in this order, each present only when it applies.
    1. `WezTerm was closed at 15:28:46 on Oct 21.` Local time, format `%H:%M:%S on %b %-d`.
    2. `The machine ran out of memory (32 GB; swap full).` The total is `f"{mem_total / GIB:.0f} GB"`. `; swap full` appears when `swap_total > 0 and swap_free == 0` in the last sample.
    3. For the biggest relevant kill: ``The kernel killed `pytest` (pid 41872) in api-gw, pane 12, which grew from 2.1 GB to 13.9 GB in 6 minutes.``
       - "From" is the first sample in the series, and "to" is its max. Minutes are rounded, at least 1.
       - A pane in no project reads `in pane 12`.
       - A pid that never joined a pane reads ``The kernel killed `pytest` (pid 41872), which cardak didn't see in a WezTerm pane.``
    4. ``systemd then stopped the whole WezTerm window, because its policy stops an app when one of its processes is killed (`OOMPolicy=stop`).`` This appears when the scope failed.
    5. Without the journal, the loud case reads `The journal wasn't readable, but memory was critical just before (1.2 GB free of 32 GB).`
    6. `The 5 biggest users then: api-gw 15.1 GB, Other 9.8 GB, ….` Projects come from the last good snapshot, plus Other's panes as `pane N · label`, plus Other's remainder as `outside WezTerm`, ordered by RSS. Omit the sentence when there is no snapshot.
    7. When the scope failed: ``To keep the window open next time, run `cardak resources install` and add the drop-in it prints.``
  - Quiet text: `WezTerm (pid 4242) closed at 15:28:46 on Oct 21; the journal shows no OOM kill.`, plus sentence 6.
  - Titles:
    - loud: `cardak · WezTerm closed: out of memory`;
    - quiet: `cardak · WezTerm closed`.

- [ ] **Step 1: Write the failing tests.** Use `TZ=UTC` plus `time.tzset()`, and build entries with `parse_entries` from the Task 1 fixtures.
  - `test_gone_after_check_delay`: a GUI pid present, then absent → `due(t + 4)` is `[]` and `due(t + 5)` has it once.
  - `test_report_oom_full_text`:
    - Setup: series of pane 12 (pid 41872 in `pids`) in api-gw grows from 2.1 to 13.9 GiB over 6 min, with swap full; `gone.ts = 1792589328.0` and the fixture scope.
    - Expected: `loud`, and the text equals the exact concatenation of sentences 1, 2, 3, 4, 6 and 7.
  - `test_one_of_two_guis_dies`: two GUIs with different scopes; the one whose scope has no kill and no failure is quiet.
  - `test_report_quiet_exit`: no kills → not loud, quiet text.
  - `test_report_without_journal_critical` and `test_report_without_journal_calm`: `kernel=None` → loud with sentence 5, and quiet.
  - `test_unknown_pid_sentence`.
- [ ] **Step 2: Run them and confirm they fail.**
  - Run: `uv run pytest tests/test_crash.py -q`
  - Expected: FAIL (module missing).
- [ ] **Step 3: Implement** (pure).
- [ ] **Step 4: Confirm they pass.**
  - Run: `uv run pytest tests/test_crash.py -q`
  - Expected: PASS.
- [ ] **Step 5: Commit** `feat(core): crash watch and report text (#57)`.

### Task 6: The daemon writes history, crash reports and start/stop

**Files:**
- Modify: `src/cardak/core/daemon.py`, `src/cardak/cli.py` (only if `daemon.main`'s call changes)
- Test: `tests/test_daemon.py`

**Interfaces:**
- Consumes: `Rollup` (Task 4), `CrashWatch` (Task 5), `read_journal` (Task 1), `gui_processes` (Task 2), `Store.record_minutes` (Task 3).
- Produces:
  - `Notify = Callable[[str, str, str, NotifySettings], None]`: (level, title, text, settings). `_notify` calls `resource_alert(level, title, text, settings)`.
  - `EventStore` protocol gains `record_minutes`.
  - `@dataclass class Daemon` with fields:
    - `source: Poller`, `store: EventStore | None`, `notify: Notify`;
    - `watcher: Watcher = field(default_factory=Watcher)`, `rollup: Rollup = …`, `crashes: CrashWatch = …`;
    - `guis: Callable[[], Mapping[int, str | None]] = dict`;
    - `journal: Callable[[float, float], tuple[list[JournalEntry] | None, list[JournalEntry]]] = lambda a, b: (None, [])`.
  - Its methods:
    - `step(now) -> list[ResourceAlert]`. Order: `guis()`, poll, `crashes.observe`, rollup rows → `record_minutes`, watcher alerts as before, then each `crashes.due(now)` → `journal(gone.ts − JOURNAL_BEFORE_S, now)` → `report`.
      - The report is recorded as `ResourceEvent(now, "crash", "critical" if loud else "info", f"wezterm:{pid}", text, loud)`.
      - It is notified when loud.
    - `started(now)` and `stopped(now)` record kind `daemon`, level `info`, subject `daemon`, with text `cardak daemon started` / `cardak daemon stopped`. `stopped` writes `rollup.flush()` first.
  - `run(daemon: Daemon, clock=time.time, sleep=time.sleep, stop=None) -> None`:
    - calls `started`, then loops as before (hourly prune, exceptions logged);
    - calls `stopped` on exit.
    - Every store write keeps the existing `StoreError` → `_log` handling.
  - `main()` wires `guis=wezterm.gui_processes` and `journal=journal.read_journal`.

- [ ] **Step 1: Rewrite the existing daemon tests onto `Daemon` and add the new ones.**
  - `test_step_writes_minutes`: steps at 0, 2 and 61 → one minute written.
  - `test_stop_flushes_partial_minute`.
  - `test_crash_is_recorded_and_notified`:
    - Setup: a fake `guis` returns `{7: "app-…scope"}` and then `{}`; a fake journal returns fixture entries.
    - Expected: at `t + 5` one `crash` event with `notified=True`, notify called with level `critical`.
  - `test_quiet_crash_not_notified`.
  - `test_journal_error_keeps_running`: the journal callable raises `OSError` → logged, the loop keeps going.
  - `test_start_and_stop_events`.
- [ ] **Step 2: Run them and confirm they fail.**
  - Run: `uv run pytest tests/test_daemon.py -q`
  - Expected: FAIL.
- [ ] **Step 3: Implement.** Keep `lock`, `ALREADY_RUNNING` and the SIGTERM handling unchanged.
- [ ] **Step 4: Confirm the full suite passes, and check imports.**
  - Run: `uv run pytest -q && uv run python -X importtime -c "import cardak.core.daemon" 2>&1 | grep -c textual`
  - Expected: PASS and `0`.
- [ ] **Step 5: Commit** `feat(daemon): minute history, crash reports and start/stop in the event log (#57)`.

### Task 7: Event log view and crash banner

**Files:**
- Create: `src/cardak/core/eventlog.py`, `src/cardak/ui/events.py`
- Modify: `src/cardak/ui/app.py`, `src/cardak/cli.py` (`_deck` opens a second `Store` as `log`; `None` on `StoreError`)
- Test: `tests/test_eventlog.py`, `tests/test_app.py`

**Interfaces:**
- Produces:
  - `LogLine(ts: float, level: str, kind: str, subject: str, text: str, full: str | None, repeats: int, until: float | None)`.
  - `log_lines(events: Sequence[ResourceEvent]) -> list[LogLine]`:
    - newest first;
    - a not-notified event folds into the preceding (older) event with the same (subject, kind): `repeats += 1`, `until = its ts`;
    - a crash's `text` is its first sentence, and `full` is the whole text when there is more than one sentence.
  - `line_text(line: LogLine) -> str`:
    - Format: `Oct 22 15:28  big  webshop · pytest in pane 12 · …`, plus `  (repeated 4× until 15:42)` when repeats > 0.
    - Kind labels: memory `memory`, big `big`, growth `growing`, busy `busy`, machine_busy `machine busy`, crash `WezTerm closed`, daemon `daemon`.
  - `ui/events.py`: `EventLogScreen(Screen[None])`.
    - It takes `events: Sequence[ResourceEvent]`.
    - It holds a `Tree` with the root hidden, one node per line, and crash lines with `full` expandable to one child per sentence.
    - `escape` pops it, and an empty log shows `No events yet. The daemon writes them: cardak resources install.`
  - In `ui/app.py`:
    - `ResourceLog` Protocol with `resource_events_since`, `latest_crash`, `dismiss_report`, `history_days`, `history_medians`, `history_hours` and `alert_counts`, using Task 3's signatures.
    - `DeckApp(..., log: ResourceLog | None = None)`.
    - Bindings `e` (event log, last 30 days) and `d` (dismiss crash), both added to `DECK_ACTIONS`.
    - A `#crash` Static under `#banner`, shown on mount when `log.latest_crash(now − 30 d)` returns one. It reads `{first sentence} · d: dismiss · e: event log`.
    - `d` dismisses it and hides it. `e` without a log notifies `Event log unavailable.`

- [ ] **Step 1: Write the failing tests.**
  - `test_repeats_fold_into_the_notified_line`.
  - `test_crash_line_first_sentence_and_full`.
  - `test_line_text_format`.
  - `test_crash_banner_and_dismiss` (pilot): a fake log with one critical crash → `#crash` displayed; press `d` → hidden and `dismiss_report` called with its ts.
  - `test_event_log_screen_lists_newest_first` (pilot): press `e` → `EventLogScreen` on top, and the first label is the newest.
  - `test_event_log_without_log_notifies`.
- [ ] **Step 2: Run them and confirm they fail.**
  - Run: `uv run pytest tests/test_eventlog.py tests/test_app.py -q`
  - Expected: the new tests FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Confirm the full suite passes.**
  - Run: `uv run pytest -q`
  - Expected: PASS.
- [ ] **Step 5: Commit** `feat(ui): event log view and the crash banner (#57)`.

### Task 8: History view and day plot

**Files:**
- Create: `src/cardak/ui/history.py`, `src/cardak/ui/plot.py`
- Modify: `src/cardak/ui/app.py` (binding `h`, in `DECK_ACTIONS`)
- Test: `tests/test_plot.py`, `tests/test_history_screen.py`

**Interfaces:**
- Consumes: `history_rows`, `sort_rows`, `edges`, `band`, `legend`, `CELLS`, `SORTS` (Task 4); `ResourceLog` (Task 7); `DeckSnapshot.thresholds` (Task 2).
- Produces:
  - `plot.braille(values: Sequence[float | None], top: float, width: int, height: int) -> list[str]`:
    - `height` rows of `width` braille characters (U+2800 base);
    - each character is 2 × 4 dots, and dot column x takes the max of its bucket of `values` (`None` when all are `None`);
    - a dot is set at the value's row, and filled vertically to the previous non-`None` column's row, so the line is continuous;
    - a `None` column has no dots (a gap).
  - `plot.level_row(value: float, top: float, height: int) -> int`: the text row of the threshold line.
  - `HistoryScreen(Screen[None])(log: ResourceLog, live: DeckSnapshot | None, now: float)`:
    - `DataTable(cursor_type="cell", cell_padding=0)`;
    - columns: name (14 wide), now / median / peak (9 each, right-aligned, GB or %), alerts (7, `-` for Other), then 30 one-character day columns;
    - day cells are `CELLS[band]`, styled by band from dim to `$error` for █; a blank for `None`;
    - an axis `Static` above shows the dates of days 0, 14 and 29 over their columns, and the legend `Static` sits below;
    - the title is `history · last 30 days · RAM` (or `CPU`), with `[c] CPU  [s] sort: peak` on the right;
    - `c` toggles the metric, `s` cycles through `SORTS`, `escape` pops.
    - `DataTable.CellSelected` on a day column pushes `PlotScreen`.
    - No history rows at all → a hint line: `No history yet: the daemon records it (cardak resources install).`
    - Without `thresholds`, the edges come from `derive(ResourceSettings(), live machine)`. Without a machine, all bands are 0.
  - `PlotScreen(Screen[None])(name: str, subject: str, metric: Metric, hours: Sequence[tuple[int, int, float]], threshold: float | None, day: date, events: Sequence[ResourceEvent], today: date)`:
    - the title is `{name} · RAM · last 30 days`;
    - the plot is `braille` over 720 hourly slots (missing hours are `None`), at width `min(screen width − 8, 120)` and height 10;
    - a threshold line `─` is drawn on `level_row` where that cell has no dots, and only for projects: Other has none;
    - the selected day's columns are drawn in reverse;
    - the x axis shows dates, and below come `Oct 21: peak 13.9 GB · 3 alerts` and that day's alert lines (`line_text`);
    - `left` / `right` move the day within the 30, and `escape` pops.

- [ ] **Step 1: Write the failing tests.**
  - `test_braille_flat_line`: constant value at half of top, width 2, height 1 → both characters equal, and the same dot row set in each.
  - `test_braille_gap`: `[1, None, None, 1]` with width 2 → the second character has dots only in its left column.
  - `test_braille_vertical_fill`.
  - `test_level_row`.
  - Pilot tests:
    - `test_history_screen_rows_and_keys`: a fake log with two projects and Other → three rows, Other last; `s` changes the sort label; `c` → the title says CPU.
    - `test_history_screen_empty_hint`.
    - `test_day_cell_opens_plot_and_arrows_move`: select a day column → `PlotScreen`; `right` changes the day line; `escape` returns to `HistoryScreen`.
- [ ] **Step 2: Run them and confirm they fail.**
  - Run: `uv run pytest tests/test_plot.py tests/test_history_screen.py -q`
  - Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Confirm the full suite passes.**
  - Run: `uv run pytest -q`
  - Expected: PASS.
- [ ] **Step 5: Commit** `feat(ui): 30-day history view and the day plot (#57)`.

### Task 9: Docs, live checks and the architecture check

**Files:**
- Modify: `AGENTS.md`, `README.md`, `CHANGELOG.md`, `PLAN.md`

- [ ] **Step 1: Update the docs.**
  - AGENTS.md:
    - Layout: add `core/{history, crash, eventlog}.py`, `collectors/journal.py`, `ui/{history, plot, events}.py`.
    - Files table: the `cardak.db` row gets minute history (30 days) and crash reports from `cardak daemon`, and dismissals from the deck.
    - Resources section: crash detection (GUI pid gone, then the journal after 5 s; loud only on OOM; journal readable with `adm` / `systemd-journal`).
  - README: the `h` / `e` / `d` keys and what the crash report says.
  - CHANGELOG `## Unreleased` → `### Added`: history view, day plot, event log, crash report and banner, written for the user, ending `(#57)`.
  - PLAN.md: tick M5 "Event log view…".
- [ ] **Step 2: Live check of the daemon.** It stays isolated, does not notify, and creates no windows.
  - Setup: scratch `XDG_STATE_HOME` / `XDG_CONFIG_HOME` with `projects.toml` containing `[notify] desktop = false` and `sound = "off"`.
  - Run 1: `cardak daemon` for 130 s.
    - Expected: ≥ 2 rows in `resource_minutes` (sqlite3 query), plus `daemon` start and stop events.
    - Record the daemon's CPU over the run with `ps -o time`. Expected: under 1% of one core.
  - Run 2, the crash path:
    - Setup: a scratch `XDG_RUNTIME_DIR/wezterm/gui-sock-<pid of a background sleep 600>`. Run the daemon with that `XDG_RUNTIME_DIR`. It can't reach WezTerm, which is fine: Other only.
    - Action: kill the `sleep`.
    - Expected: within about 10 s, one `crash` event with level `info`, and no notification.
- [ ] **Step 3: Timing check on a synthetic DB.**
  - Setup: 500k `resource_minutes` rows over 20 subjects × 30 days.
  - Run: time `history_days` + `history_medians`.
  - Expected: < 300 ms. If over, load the history in a thread worker in `HistoryScreen`, and record that.
- [ ] **Step 4: Run CI locally and the architecture check.**
  - Run: `uv run ruff check && uv run ruff format --check && uv run pyright && uv run pytest --cov --cov-branch --cov-fail-under=80 -q`
  - Expected: all clean.
  - Then run `enola check`. Fix any cycle or layer crossing. Record the accepted complexity and hotspot findings in AGENTS.md's enola section under a "M5 part 3" bullet.
- [ ] **Step 5: Commit** `docs: history, event log and crash report in AGENTS.md, README, PLAN and the changelog (#57)`.

## Rulings made while writing this plan

- **Header sparklines stay in memory** (part 1), not read from the daemon. Cost if wrong: they start empty at each deck start.
- **The plot is hand-drawn braille** rather than `textual-plotext`, because there is no new dependency and it is pure and testable. Cost if wrong: a plainer plot.
- **Other uses the same bands as projects**, so it can look red although it never alerts. Cost if wrong: one more edge set.
- **Without journal access, memory critical in the last 30 s makes a crash loud.** The spec allows a samples-only fallback, and otherwise nothing could ever be loud for users outside `adm` / `systemd-journal`. Cost if wrong: one false alarm after an unrelated quit while memory was critical.
- **The fix line in the report points to `cardak resources install`** instead of repeating the drop-in, because `core` cannot import `systemd/`. The scope failing with `oom-kill` already proves its policy was `stop`. Cost if wrong: one extra command for the user.
- **Suppressed repeats are folded into their notified line** in the event-log view. The store still has every row. Cost if wrong: less detail on screen.
- **The deck reads the log through a second `Store` connection**, so the UI thread never shares the poll worker's connection.
- **Schema 4 makes 0.7.0 refuse the database** (the same behaviour #54 describes). Cost if wrong: downgrading means losing agent status until you move `cardak.db` aside.
- **An OOM kill that doesn't close WezTerm** (after the user applies `OOMPolicy=continue`) is not reported in this part. It is deferred.
