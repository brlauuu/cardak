# M5 part 2: daemon and alerts — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A background service (`cardak daemon`, a systemd user unit) watches the machine and
every WezTerm pane, and sends a desktop notification naming the project, pane and command when
memory gets tight or a project or pane gets too big, grows too fast or stays too busy. `cardak
resources install` sets it up, writes the thresholds into projects.toml and says how to stop one
OOM kill from closing a whole WezTerm window.

**Architecture:** The daemon reuses `LiveSource` (part 1) for sampling: each ~2 s poll gives a
`DeckSnapshot` with projects, panes, Docker, Other and the machine. A pure `core/watch.py`
`Watcher` turns successive snapshots into `ResourceAlert`s (sustain timers, quiet rules,
escalation, culprit text). The daemon writes every alert to a new `resource_events` table and
notifies through `core/alerts.py`. A new `systemd/` package writes the unit and checks
`OOMPolicy`, importing nothing from cardak.

**Tech Stack:** Python 3.13, SQLite, `notify-send`, `systemctl --user`, tomlkit.

**Spec:** `docs/superpowers/specs/2026-10-22-m5-resources-design.md` (part 2 of "Delivery";
sections Alerts, Daemon and install, Config, the OOMPolicy paragraph of Crash report).

## Global Constraints

- cardak never kills, pauses, renices or limits a process, never jumps to or focuses a pane on an
  alert, and never applies the OOMPolicy fix itself; it prints it.
- Thresholds: available RAM warning < `ram_warn_available` **for 10 s**; critical < `ram_critical_available`
  or swap full (immediate); project/pane RAM > `pane_ram`; growth > `growth_per_min` sustained
  `sustain_s`; project/pane CPU > `pane_cpu_pct` for `sustain_s`; machine CPU > `machine_cpu_pct`
  for `sustain_s` (part 1's `derive` gives them).
- Quiet rules: at most one notification per culprit per alert kind per `repeat_s` (10 min);
  warning → critical notifies immediately; nothing on recovery; every alert, including
  suppressed repeats, goes to the event log.
- Culprits: every project (config or git group) and every pane in no project. Other as a whole
  never gets big/growth/busy alerts. A pane pushing its project over is **one** notification,
  keyed on the project, naming the pane.
- Critical uses `notify-send -u critical` and a sound; warnings normal urgency and the
  `[notify] sound` setting; `[notify] desktop = false` turns notifications off (sound too).
- Only the daemon alerts; the deck never notifies about resources.
- Daemon: no Textual import, its own systemd unit `cardak.service`, `Restart=on-failure`,
  `wezterm cli --no-auto-start` (already in `collectors/wezterm.py`).
- `cardak resources install` asks before writing the unit (or `--yes`); writes `[resources]`
  defaults only for keys that are absent (`--refresh-defaults` recomputes them all), each with a
  comment saying how it was derived; checks `OOMPolicy` and prints the fix when it is `stop`.
- Verified on the dev machine (2026-10-23): a prefix drop-in
  `app-org.wezfurlong.wezterm-.scope.d/*.conf` with `[Scope]` / `OOMPolicy=continue` applies to
  `app-org.wezfurlong.wezterm-<id>.scope`; with it the scope survives an OOM kill of one of its
  processes, without it the scope fails with `oom-kill`.
- `cardak hook`'s import budget is untouched (`HOOK_FORBIDDEN` in `tests/test_cli.py`).
- Layering: `systemd/` imports nothing from cardak; `core` never imports it; `cli.py` wires them.
- Tests never touch the live machine, the real state dir, `~/.config` or the user's systemd:
  subprocess runners and paths are injected.
- Commits end `(#57)`; nothing personal or machine-specific in committed files.

## Review Focus

1. **Two daemons at once** (the service plus a hand-run `cardak daemon`) — the second exits with
   a message instead of doubling every notification. (Task 4 `test_second_daemon_exits`.)
2. **A condition that flaps** around a threshold (pane at 10.4 → 10.6 → 10.4 GB every poll) —
   at most one notification per 10 min, not one per crossing. (Task 3 `test_flapping_notifies_once`.)
3. **A poll that raises** (WezTerm restarting, a collector bug) — the daemon logs it and keeps
   running; the watcher keeps its timers. (Task 4 `test_poll_error_keeps_running`.)
4. **`cardak` not on PATH / `systemctl` failing** during install — a clear message and exit 1,
   nothing half-written left enabled. (Task 6 `test_install_without_cardak_on_path`,
   `test_install_reports_systemctl_failure`.)
5. **A pane that disappears** while its sustain timer runs (pane closed during a busy build) —
   its timers are dropped; a new pane with the same id starts fresh. (Task 3
   `test_closed_pane_forgets_its_timers`.)

---

### Task 1: The event log table

**Files:**
- Modify: `src/cardak/core/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Produces:
  ```python
  SCHEMA_VERSION = 3
  RESOURCE_KEEP_SECONDS = 30 * 86_400
  @dataclass(frozen=True)
  class ResourceEvent:
      ts: float
      kind: str       # "memory" | "big" | "growth" | "busy" | "machine_busy"
      level: str      # "warning" | "critical"
      subject: str    # "machine" | "project:<path>" | "pane:<id>"
      text: str
      notified: bool
  Store.record_resource_event(self, event: ResourceEvent) -> None
  Store.resource_events_since(self, ts: float) -> list[ResourceEvent]  # oldest first
  ```
  Table `resource_events(ts REAL, kind TEXT, level TEXT, subject TEXT, text TEXT, notified INTEGER)`
  with an index on `ts`; `prune` deletes rows older than 30 days. `ResourceEvent` lives in
  `store.py` and takes only primitives, so the hook's imports stay as they are.

- [ ] **Step 1: Write the failing tests**

```python
def test_resource_events_round_trip(tmp_path: Path) -> None:
    store = Store(tmp_path / "c.db")
    e = ResourceEvent(10.0, "big", "warning", "project:/r/webshop", "webshop · pytest …", True)
    store.record_resource_event(e)
    store.record_resource_event(replace(e, ts=20.0, notified=False))
    assert store.resource_events_since(15.0) == [replace(e, ts=20.0, notified=False)]
    assert store.resource_events_since(0.0)[0] == e

def test_resource_events_pruned_after_30_days(tmp_path: Path) -> None:
    store = Store(tmp_path / "c.db")
    store.record_resource_event(ResourceEvent(0.0, "memory", "critical", "machine", "x", True))
    store.prune(31 * 86_400.0)
    assert store.resource_events_since(-1.0) == []

def test_schema_2_database_gains_the_table(tmp_path: Path) -> None:
    # create a v2 db (agent_sessions/events/summaries, user_version = 2) by hand, open it with
    # Store → user_version == 3 and record_resource_event works; existing rows survive
```

- [ ] **Step 2: Run** `uv run pytest tests/test_store.py -q` — new tests FAIL.
- [ ] **Step 3: Implement.** Add the table to `_SCHEMA` (`CREATE TABLE IF NOT EXISTS`, so the
  existing `version < SCHEMA_VERSION` path migrates); wrap `sqlite3.Error` in `StoreError` as the
  other methods do.
- [ ] **Step 4: Run** `uv run pytest -q` — PASS, including `tests/test_cli.py` hook guards.
- [ ] **Step 5: Commit** `feat(core): resource event log in cardak.db (#57)`

---

### Task 2: Resource notifications

**Files:**
- Modify: `src/cardak/core/alerts.py`
- Test: `tests/test_alerts.py`

**Interfaces:**
- Produces:
  ```python
  def resource_argv(level: str, title: str, body: str) -> list[str]
      # ["notify-send", "-a", "cardak", "-u", "critical"|"normal", title, body]; body cut to MAX_BODY*2
  def resource_alert(level: str, title: str, body: str, settings: NotifySettings, *,
                     spawn: Spawn = spawn_detached, rng: random.Random | None = None,
                     which: Which = shutil.which) -> None
      # desktop off → nothing at all; else notify-send, then a sound as `alert` chooses it
  ```

- [ ] **Step 1: Write the failing tests**

```python
def test_resource_argv_levels() -> None:
    assert resource_argv("critical", "cardak · memory critical", "x") == [
        "notify-send", "-a", "cardak", "-u", "critical", "cardak · memory critical", "x"]
    assert resource_argv("warning", "t", "b")[4] == "normal"
    assert len(resource_argv("warning", "t", "y" * 1000)[-1]) == 2 * MAX_BODY

def test_resource_alert_spawns_notification_and_sound() -> None:
    # settings desktop=True, sound="random" → two spawns: notify-send …, then a player argv

def test_resource_alert_off_when_desktop_false() -> None:
    # desktop=False → no spawns
```

- [ ] **Step 2: Run** `uv run pytest tests/test_alerts.py -q` — FAIL.
- [ ] **Step 3: Implement**, sharing the sound choice with `alert` (one private helper).
- [ ] **Step 4: Run** `uv run pytest -q` — PASS.
- [ ] **Step 5: Commit** `feat(core): resource notifications (#57)`

---

### Task 3: The watcher

**Files:**
- Create: `src/cardak/core/watch.py`
- Test: `tests/test_watch.py`

**Interfaces:**
- Consumes: `DeckSnapshot`, `Group`, `PaneRow`, `DockerLine` (snapshot), `Thresholds`,
  `memory_level` (resources), `MachineSample`.
- Produces:
  ```python
  AlertKind = Literal["memory", "big", "growth", "busy", "machine_busy"]
  MEMORY_SUSTAIN_S = 10.0
  LOG_EVERY_S = 60.0   # a condition that persists is logged (not notified) at most once a minute

  @dataclass(frozen=True)
  class ResourceAlert:
      ts: float
      kind: AlertKind
      level: Literal["warning", "critical"]
      subject: str      # "machine" | "project:<path>" | "pane:<id>"
      title: str        # "cardak · memory critical", "cardak · api-gw is big", "… growing fast", "… busy", "cardak · machine busy"
      text: str
      notify: bool

  class Watcher:
      def __init__(self) -> None
      def step(self, snap: DeckSnapshot, t: Thresholds, now: float) -> list[ResourceAlert]

  def culprit(snap: DeckSnapshot, subject_group: Group | None, pane: PaneRow | None) -> str
  def gb(n: int) -> str   # "13.9 GB"
  ```

Rules:
- **Subjects:** each group of kind `config`/`git` → `project:<key>`; each pane in the `other`
  group → `pane:<pane_id>`; plus `machine`. The waiting group is ignored (its rows are copies).
  For a project, RAM and CPU are the group's totals; for a pane, `rss + mcp_rss` and
  `cpu_pct + mcp_cpu_pct`.
- **Conditions** (true/false each step, with the time it became true kept per (subject, kind)):
  - `memory` (subject `machine`): `memory_level(...)`; `warning` counts only once true for
    `MEMORY_SUSTAIN_S`; `critical` at once.
  - `big`: RAM > `t.pane_ram`, at once.
  - `growth`: the subject's RAM now minus its RAM at the newest sample at least `t.sustain_s` old
    ≥ `t.growth_per_min × t.sustain_s / 60`. Keep `(ts, rss)` per subject for `sustain_s + 10` s.
  - `busy`: CPU > `t.pane_cpu_pct` continuously for `t.sustain_s`.
  - `machine_busy`: `machine.cpu_pct > t.machine_cpu_pct` continuously for `t.sustain_s`
    (`None` CPU counts as not busy).
- **Firing:** when a condition holds, emit an alert with `notify=True` if this (subject, kind) was
  never notified, or its last notification is ≥ `t.repeat_s` old, or the level rose from
  `warning` to `critical`. Otherwise emit `notify=False`, but at most once per `LOG_EVERY_S` per
  (subject, kind). A condition going false emits nothing and does **not** reset the notify clock.
- **Forgetting:** a subject not in this snapshot loses its timers and series (not its notify
  clock — a pane id reused within 10 min stays quiet; acceptable).
- **Text** (spec "Alerts"), `GIB`-based with one decimal:
  - project: `"{name} · {biggest} · {gb(piece)} of the project's {gb(total)}"` then
    `", +{rate:.1f} GB/min"` for growth, or CPU instead of RAM for busy
    (`"{name} · {biggest} · {cpu:.0f}% CPU of the project's {total:.0f}%"`), then
    `" · RAM {used_pct}% used"`. `biggest` is the pane with the most RAM (`f"{row.label} in pane {id}"`)
    or a Docker line (`line.label`) when that is bigger.
  - pane in no project: `"pane {id} · {label} · {gb(rss)}"` (+ growth/CPU part) `" · RAM {pct}% used"`.
  - memory: `"{gb(available)} free of {gb(total)} ({pct}% free)"` + `", swap full"` when so, then
    `" · biggest: {culprit}"` where culprit is the project or no-project pane with the most RAM, in
    the project form above without the trailing RAM part; or `"most of it is outside WezTerm"` when
    the Other group's `outside_rss` is larger than every project; or a build line
    `"Docker builds, N panes"` when that is on Other and larger.
  - machine_busy: `"CPU {pct:.0f}% for {sustain_s/60:.0f} min · busiest: {culprit by CPU}"`.

- [ ] **Step 1: Write the failing tests** (snapshots built with small helpers, a 32 GiB/16-core
  `Thresholds` from `derive`, explicit `now` values):

```python
def test_big_project_notifies_once_then_logs_quietly() -> None:
    # webshop group 9 GiB (pane 12 "pytest" 8.5 GiB) → step at 0: one alert, kind "big",
    # subject "project:/home/user/repos/webshop", notify True,
    # text == "webshop · pytest in pane 12 · 8.5 GB of the project's 9.0 GB · RAM 50% used"
    # step at 2: []  (inside LOG_EVERY_S); step at 61: notify False; step at 601: notify True

def test_flapping_notifies_once() -> None:
    # alternate 9 GiB / 7 GiB every 2 s for 5 min → exactly one notify=True alert

def test_memory_warning_needs_10_seconds_critical_does_not() -> None:
    # available 4 GiB of 32: steps at 0, 5 → []; at 10 → warning notify True
    # then available 2 GiB at 12 → critical notify True (escalation inside the window)

def test_memory_text_names_the_culprit_or_outside() -> None:
    # biggest project api-gw → text endswith "biggest: api-gw · pytest in pane 3 · 6.0 GB of the project's 6.5 GB"
    # Other.outside_rss 20 GiB, projects ≤ 6.5 GiB → endswith "biggest: most of it is outside WezTerm"
    # swap_total > 0 and swap_free == 0 → "…, swap full · biggest: …"

def test_growth_over_the_sustain_window() -> None:
    # pane in no project: rss 1 GiB at 0, +1.2 GiB/min after → at 120: kind "growth",
    # subject "pane:7", text contains "+1.2 GB/min"; at 60 nothing (window not yet covered)

def test_busy_needs_sustain() -> None:
    # project CPU 900% from 0: at 118 nothing; at 120 kind "busy", text contains "900% CPU"

def test_machine_busy() -> None:
    # machine cpu 95% from 0 → at 120 kind "machine_busy", subject "machine"; cpu None resets

def test_other_is_never_a_culprit_of_big() -> None:
    # Other group 20 GiB, no projects → no "big"/"growth"/"busy" alerts with a project subject;
    # its panes are judged one by one

def test_one_alert_when_a_pane_pushes_its_project_over() -> None:
    # webshop with one 9 GiB pane → exactly one "big" alert, subject project:…, none for pane:…

def test_closed_pane_forgets_its_timers() -> None:
    # pane 7 (no project) busy from 0; gone at 60; back at 62 busy → no busy alert at 122,
    # one at 182

def test_waiting_group_is_ignored() -> None:
    # same pane in "Waiting on you" and in webshop → one alert, not two
```

- [ ] **Step 2: Run** `uv run pytest tests/test_watch.py -q` — FAIL.
- [ ] **Step 3: Implement** `core/watch.py`: a `_Condition` helper per (subject, kind) holding
  `since`, `last_notified`, `last_level`, `last_logged`; `Watcher.step` builds the subject list,
  evaluates each kind with one small function per kind, and hands firing to one `_fire` helper.
  Keep each function small (enola flags complexity outliers).
- [ ] **Step 4: Run** `uv run pytest -q` — PASS.
- [ ] **Step 5: Commit** `feat(core): resource alerts with sustain and quiet rules (#57)`

---

### Task 4: `cardak daemon`

**Files:**
- Create: `src/cardak/core/daemon.py`
- Modify: `src/cardak/core/source.py` (public `config` property), `src/cardak/cli.py`
  (`daemon` subcommand)
- Test: `tests/test_daemon.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `LiveSource` (store=None, key_file=None, on_config=None), `Watcher`,
  `derive`, `Store.record_resource_event`/`prune`, `resource_alert`.
- Produces:
  ```python
  INTERVAL = 2.0
  PRUNE_EVERY_S = 3600.0
  class DaemonRunning(Exception): ...
  def lock(path: Path) -> IO[str]            # fcntl.flock LOCK_EX|LOCK_NB on state_dir()/daemon.lock; raises DaemonRunning
  def step(source: Poller, watcher: Watcher, store: EventStore | None, notify: Notify, now: float) -> list[ResourceAlert]
  def run(source: Poller, watcher: Watcher, store: EventStore | None, notify: Notify,
          clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep,
          stop: threading.Event | None = None) -> None
  def main() -> int    # what `cardak daemon` runs
  LiveSource.config -> Config   # property, under the lock
  ```
  `Poller` is a Protocol with `poll() -> DeckSnapshot` and `config: Config`; `Notify` is
  `Callable[[ResourceAlert, NotifySettings], None]` (default: `resource_alert(a.level, a.title,
  a.text, settings)`); `EventStore` a Protocol with `record_resource_event` and `prune`.

Rules:
- `step`: poll; when `snap.machine` is None, return [] (no thresholds without a machine);
  `derive(source.config.resources, mem_total, n_cpus)`; `watcher.step`; every alert →
  `store.record_resource_event(ResourceEvent(a.ts, a.kind, a.level, a.subject, a.text, a.notify))` (StoreError logged to stderr, never raised); `notify` for
  `notify=True` ones.
- `run`: `step` every `INTERVAL`, `store.prune(now)` every `PRUNE_EVERY_S`, any exception from
  a step printed to stderr with a timestamp and the loop goes on; returns when `stop` is set.
- `main`: `lock(state_dir() / "daemon.lock")` (on `DaemonRunning`: print
  `"cardak daemon: already running"` to stderr, return 1); open `Store(db_path())` (on
  `StoreError`: run without a store and say so on stderr); `SIGTERM`/`SIGINT` set `stop`;
  `LiveSource(config_path(None))`; `run(...)`; return 0.
- `cli.py`: `sub.add_parser("daemon", help="watch resources and alert (run by the systemd service)")`;
  dispatch imports `cardak.core.daemon` inside the branch.

- [ ] **Step 1: Write the failing tests**

```python
def test_step_records_and_notifies() -> None:
    # fake source: one 9 GiB webshop group + machine 32 GiB/16 → one record, one notify call;
    # second step 2 s later → no record (LOG_EVERY_S), no notify

def test_step_without_machine_does_nothing() -> None:

def test_store_error_does_not_stop_notifying() -> None:
    # store.record_resource_event raises StoreError → notify still called, no exception

def test_poll_error_keeps_running() -> None:
    # source.poll raises RuntimeError on the first call, then works; run with a fake clock/sleep
    # and a stop event set after 3 steps → 2 successful steps, stderr has "RuntimeError"

def test_prunes_hourly() -> None:
    # fake clock advancing 2 s per sleep; stop after 3601 s → prune called twice (start, +3600)

def test_second_daemon_exits(tmp_path: Path) -> None:
    held = lock(tmp_path / "daemon.lock")
    with pytest.raises(DaemonRunning):
        lock(tmp_path / "daemon.lock")
    held.close()
    lock(tmp_path / "daemon.lock").close()

# test_cli.py
def test_daemon_does_not_import_textual(tmp_path: Path) -> None:
    # subprocess: import cardak.core.daemon; assert "textual" not in sys.modules and
    # "cardak.ui" not in sys.modules

def test_daemon_dispatches(monkeypatch) -> None:
    # monkeypatch cardak.core.daemon.main → main(["daemon"]) returns its value
```

- [ ] **Step 2: Run** `uv run pytest tests/test_daemon.py tests/test_cli.py -q` — new tests FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest -q` — PASS.
- [ ] **Step 5: Commit** `feat: cardak daemon (#57)`

---

### Task 5: Writing the `[resources]` defaults

**Files:**
- Modify: `src/cardak/core/config.py`, `src/cardak/core/resources.py`
- Test: `tests/test_config.py`, `tests/test_resources.py`

**Interfaces:**
- Produces:
  ```python
  # resources.py
  def default_entries(mem_total: int, n_cpus: int) -> list[tuple[str, float, str]]
      # (key, value, comment) in this order:
      # ("ram_warn_available_gb", round(0.15 * total_gib, 1), "15% of {total_gib:.0f} GB")
      # ("ram_critical_available_gb", round(0.07 * total_gib, 1), "7% of {total_gib:.0f} GB")
      # ("pane_ram_gb", round(0.25 * total_gib, 1), "25% of {total_gib:.0f} GB")
      # ("growth_gb_per_min", 1.0, "")
      # ("pane_cpu_pct", 50.0 * n_cpus, "half of {n_cpus} cores")
      # ("machine_cpu_pct", 90.0, ""), ("sustain_s", 120.0, ""), ("repeat_min", 10.0, "")
  # config.py
  def write_resource_defaults(path: Path, entries: Sequence[tuple[str, float, str]],
                              refresh: bool = False) -> list[str]
      # adds missing keys to [resources] (creating the table at the end if absent), each with its
      # comment; refresh=True overwrites every listed key; returns the keys it wrote; atomic;
      # keeps the rest of the file byte-for-byte (tomlkit)
  ```
  Whole numbers are written as integers (`800`, not `800.0`); the parser accepts both.

- [ ] **Step 1: Write the failing tests**

```python
def test_default_entries_for_32_gib_16_cores() -> None:
    entries = dict((k, (v, c)) for k, v, c in default_entries(32 * GIB, 16))
    assert entries["ram_warn_available_gb"] == (4.8, "15% of 32 GB")
    assert entries["ram_critical_available_gb"] == (2.2, "7% of 32 GB")
    assert entries["pane_ram_gb"] == (8.0, "25% of 32 GB")
    assert entries["pane_cpu_pct"] == (800.0, "half of 16 cores")

def test_write_resource_defaults_adds_missing_keys_only(tmp_path) -> None:
    # file with a project, a comment line, and "[resources]\npane_ram_gb = 4 # mine\n" →
    # written == every key but pane_ram_gb; pane_ram_gb line unchanged; the project and comment
    # unchanged; load(file).resources.ram_warn_available_gb == 4.8; text contains "# 15% of 32 GB"

def test_write_resource_defaults_refresh_overwrites(tmp_path) -> None:
    # same file, refresh=True → pane_ram_gb == 8.0; the rest of the file unchanged

def test_write_resource_defaults_creates_file_and_table(tmp_path) -> None:

def test_write_resource_defaults_twice_writes_nothing(tmp_path) -> None:
    # second call returns [] and leaves the file's mtime alone
```

- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** with `tomlkit` imported inside the function (config.py's import rule)
  and `_atomic_write`.
- [ ] **Step 4: Run** `uv run pytest -q` — PASS.
- [ ] **Step 5: Commit** `feat(core): write [resources] defaults from the machine (#57)`

---

### Task 6: `cardak resources install | uninstall`

**Files:**
- Create: `src/cardak/systemd/__init__.py`, `src/cardak/systemd/install.py`
- Modify: `src/cardak/cli.py`
- Test: `tests/test_systemd_install.py`, `tests/test_cli.py`

**Interfaces:**
- Produces (`systemd/install.py`, importing nothing from cardak):
  ```python
  MARKER = "# cardak: generated"
  UNIT = "cardak.service"
  SCOPE_PREFIX = "app-org.wezfurlong.wezterm-"
  DROPIN_DIR = "app-org.wezfurlong.wezterm-.scope.d"
  Run = Callable[[list[str]], subprocess.CompletedProcess[str]]

  def unit_dir(env: Mapping[str, str] | None = None) -> Path      # $XDG_CONFIG_HOME/systemd/user
  def unit_text(cardak_bin: Path) -> str
  def is_ours(path: Path) -> bool                                  # first line == MARKER
  def write_unit(path: Path, text: str, force: bool = False) -> None  # atomic; refuses a non-ours file unless force (raises FileExistsError)
  def enable(run: Run) -> str | None        # daemon-reload, enable --now; the error text or None
  def disable(run: Run) -> str | None       # disable --now (ignores "not loaded"), daemon-reload
  def oom_policies(run: Run) -> dict[str, str]   # running WezTerm scopes → OOMPolicy
  def oom_advice(dropin_root: Path) -> str  # the exact lines below
  ```
  `unit_text`:
  ```
  # cardak: generated — `cardak resources uninstall` removes it
  [Unit]
  Description=cardak: resource alerts for WezTerm panes

  [Service]
  ExecStart=<cardak_bin> daemon
  Restart=on-failure
  RestartSec=5

  [Install]
  WantedBy=default.target
  ```
  (`is_ours` checks the line starts with `MARKER`.)
  `oom_advice` (exact copy):
  ```
  When memory runs out, the kernel kills one process, but systemd then closes the whole
  WezTerm window it ran in (OOMPolicy=stop). To keep the window and lose only that process:

    mkdir -p <dropin_root>/app-org.wezfurlong.wezterm-.scope.d
    printf '[Scope]\nOOMPolicy=continue\n' > <dropin_root>/app-org.wezfurlong.wezterm-.scope.d/oom.conf
    systemctl --user daemon-reload

  WezTerm windows opened after that keep running when one of their processes is killed.
  ```
- `cli.py` `_resources(command, yes, refresh, force)`:
  - `install`: `cardak_bin = shutil.which("cardak")` (None → print
    `"cardak isn't on PATH; install it with uv tool install first"` to stderr, return 1);
    print the unit path and text; ask `Write <path> and start the service? [y/N]` unless
    `--yes`; `write_unit`; `enable` (error → print it, return 1); `MachineSampler().sample()`
    → `write_resource_defaults(config_path(...), default_entries(...), refresh)` and print the keys
    written; `oom_policies` → if any is `stop` and `<unit_dir>/app-org.wezfurlong.wezterm-.scope.d/`
    has no `.conf` with `OOMPolicy=continue`, print `oom_advice(unit_dir())`. Return 0.
  - `uninstall`: `disable`; remove the unit if `is_ours`; print what happened. Return 0.
  - Parser: `resources install [--yes] [--refresh-defaults] [--force]`, `resources uninstall`.

- [ ] **Step 1: Write the failing tests** (fake `Run` recording argv and returning
  `CompletedProcess`; `tmp_path` for every directory):

```python
def test_unit_text_runs_the_daemon() -> None:
    text = unit_text(Path("/home/user/.local/bin/cardak"))
    assert text.startswith(MARKER) and "ExecStart=/home/user/.local/bin/cardak daemon" in text
    assert "Restart=on-failure" in text and "WantedBy=default.target" in text

def test_write_unit_refuses_a_foreign_file(tmp_path) -> None:
def test_write_unit_replaces_its_own(tmp_path) -> None:
def test_enable_runs_reload_then_enable() -> None:
    # argv == [["systemctl","--user","daemon-reload"], ["systemctl","--user","enable","--now","cardak.service"]]
def test_enable_reports_failure() -> None:
    # second call returncode 1, stderr "Failed to enable" → returns "Failed to enable"
def test_oom_policies_lists_wezterm_scopes() -> None:
    # list-units output with two wezterm scopes and one other → show called for the two;
    # returns {"app-org.wezfurlong.wezterm-a.scope": "stop", "...-b.scope": "continue"}
def test_oom_advice_text(tmp_path) -> None:
    assert "OOMPolicy=continue" in oom_advice(tmp_path) and str(tmp_path) in oom_advice(tmp_path)

# test_cli.py — monkeypatch shutil.which, the runner, unit_dir, MachineSampler and config path
def test_install_without_cardak_on_path(...) -> None:     # exit 1, nothing written
def test_install_reports_systemctl_failure(...) -> None:  # exit 1, message printed
def test_install_writes_unit_defaults_and_advice(...) -> None:
    # --yes: unit written, enable called, [resources] keys written, advice printed when a scope is "stop"
def test_install_skips_advice_when_dropin_exists(...) -> None:
def test_install_asks_and_no_writes_nothing(...) -> None:  # input "n" → exit 1, no unit
def test_uninstall_removes_our_unit(...) -> None:
```

- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.** `systemd/install.py` does its own atomic write (tempfile + `os.replace`,
  mode 0644): it may not import `cardak.wezterm.files`.
- [ ] **Step 4: Run** `uv run pytest -q` — PASS; `test_hook_imports_only_what_it_needs` still green.
- [ ] **Step 5: Commit** `feat: cardak resources install sets up the alert service (#57)`

---

### Task 7: The deck says when alerts are off

**Files:**
- Modify: `src/cardak/cli.py` (`_resources_hint`), `src/cardak/ui/app.py` (`resources_status`)
- Test: `tests/test_cli.py`, `tests/test_app.py`

**Interfaces:**
- Produces: `DeckApp(..., resources_status: Callable[[], str | None] | None = None)`, shown once on
  mount as a warning notification, like `hooks_status`. `cli._resources_hint() -> str | None`:
  `None` when `unit_dir() / UNIT` exists, else
  `"Resource alerts are off with the deck closed. Run `cardak resources install` to turn them on."`

- [ ] **Step 1: Write the failing tests** — `test_resources_hint_once` (app, like `test_hooks_hint_once`),
  `test_resources_hint_none_when_installed` (cli, tmp unit dir).
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement**; pass `resources_status=_resources_hint` in `_deck`.
- [ ] **Step 4: Run** `uv run pytest -q` — PASS.
- [ ] **Step 5: Commit** `feat(ui): say when resource alerts are off (#57)`

---

### Task 8: Docs, checks and a live run

**Files:**
- Modify: `AGENTS.md` (Layout: `core/{watch, daemon}.py`, `systemd/install.py`; layering line adds
  `systemd/`; "Files cardak writes" gains `~/.config/systemd/user/cardak.service` (`cardak resources
  install` / `uninstall`, after asking) and `~/.local/state/cardak/daemon.lock`; `cardak.db` row
  mentions `resource_events` (30 days); Resources section: the alert rules, quiet rules, the verified
  OOMPolicy drop-in)
- Modify: `README.md` "Resources": `cardak resources install`, what an alert looks like, the
  OOMPolicy advice
- Modify: `CHANGELOG.md` `## Unreleased` → `### Added`:
  - `A desktop notification when memory runs low or a project or pane gets too big, grows too fast or stays too busy, naming the project, pane and command; at most one every 10 minutes per project, and it keeps watching with the deck closed. Run `cardak resources install` once. (#57)`
  - `cardak resources install` writes the thresholds it uses into `[resources]` in projects.toml, worked out from your machine, and tells you how to keep a WezTerm window open when the kernel kills one of its processes for lack of memory. (#57)
- Modify: `PLAN.md` §11 M5: tick "Spike alerts with attribution" and "Daemon".

- [ ] **Step 1:** `uv run ruff check && uv run ruff format --check && uv run pyright && uv run pytest --cov --cov-branch --cov-fail-under=80`
  — green.
- [ ] **Step 2:** `enola check .` — no cycles, no layer crossing; split real outliers once, record
  accepted coordinators in AGENTS.md "Architecture checks".
- [ ] **Step 3: Live run, without installing the service:** scratch `XDG_STATE_HOME`, a scratch
  projects.toml with `[notify] desktop = false` and `[resources] pane_ram_gb = 0.2`, run
  `cardak daemon` in the foreground for ~10 s (`timeout 12`), then read `resource_events` from the
  scratch `cardak.db`: at least one `big` row naming a real project or pane; a second
  `cardak daemon` started meanwhile exits 1 with "already running". Measure the daemon's CPU
  (`ps -o %cpu`) over the run and record it in AGENTS.md Resources.
- [ ] **Step 4: Commit** `docs: daemon and alerts in AGENTS.md, README, PLAN and the changelog (#57)`
