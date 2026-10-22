# M5 part 1: live numbers — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The deck shows, live, what each project and pane costs the machine: RAM with a short
sparkline and CPU on every row, MCP servers and Docker work as their own lines, Other as the
remainder, and the machine total at the top (#33). No daemon yet.

**Architecture:** Two new collectors read the machine (`/proc/meminfo`, psutil CPU) and Docker
(Engine API over its unix socket, cgroup v2 files). Three pure core modules attribute Docker work
to projects and compute Other (`accounting`), derive thresholds from the machine and
`[resources]` (`resources`), and keep a short in-memory history for sparklines and "over
threshold" marks (`recent`). `LiveSource._poll` runs them after `build_snapshot`; the UI renders
the new fields.

**Tech Stack:** Python 3.13, psutil, stdlib `http.client` over `socket.AF_UNIX`, Textual/Rich.

**Spec:** `docs/superpowers/specs/2026-10-22-m5-resources-design.md` (part 1 of "Delivery").

## Global Constraints

- Layering (AGENTS.md): `collectors` import nothing from cardak; `core` never imports `ui`;
  all Docker access lives in `collectors/docker.py`, all `/proc/meminfo` and machine CPU reading
  in `collectors/machine.py`.
- cardak only reads Docker: `GET /containers/json`; no other endpoint, no writes.
- If Docker is not installed or not reachable, Docker usage silently falls into Other.
- Machine used = `MemTotal − MemAvailable`. Other is clamped at 0; no row shows a negative number.
- CPU is percent where 100% = one core, as the deck does today; the machine header alone shows
  whole-machine percent (0–100).
- "GB" in `[resources]` keys means GiB (2**30 bytes), matching `fmt_rss`.
- Defaults (spec "Alerts"): available RAM warning < 15% of total, critical < 7% or swap full;
  pane/project RAM > 25% of total; growth > 1 GB/min; pane/project CPU > 50% × cores (in deck %);
  machine CPU > 90%; sustain 120 s; repeat 10 min.
- Other as a whole is never marked over a threshold. Panes in no project can be.
- `cardak hook` imports nothing new: `test_hook_imports_only_what_it_needs` must stay green.
- Tests use fixtures and fakes, never the live machine (except the existing marked live tests).
- CI: `ruff check`, `ruff format --check`, `pyright`, `pytest --cov --cov-branch --cov-fail-under=80`.
- Commits: conventional, ending `(#57)`; nothing personal or machine-specific in committed files
  or fixtures (sanitize paths to `/home/user/...`, use example projects `webshop`, `api-gw`).

## Review Focus

1. **Docker missing, socket permission denied, or the daemon stopped** — the deck polls on
   without delay or warning, and Docker usage lands in Other. (Task 2 test
   `test_sampler_without_docker_still_reads_builds`, `test_list_containers_unreachable`.)
2. **A container stops between listing and the cgroup read** — that container is skipped, no
   exception. (Task 2 `test_vanished_cgroup_is_skipped`.)
3. **Σ projects > machine used** (RSS double-counts shared pages) — Other and the
   "outside WezTerm" line show 0, never negative. (Task 5 `test_other_clamped_at_zero`.)
4. **First sample** — Docker and machine CPU have no delta yet; they show `–`, not `0%`.
   (Task 1 `test_first_cpu_sample_is_none`, Task 2 `test_first_container_cpu_is_none`.)
5. **A container mounting a folder above every project (e.g. the home dir), and nested
   projects** — a mount matches only a project that contains it; the deepest project wins; a
   home-dir mount matches none. (Task 5 `test_mount_matching`.)

---

### Task 1: Machine collector

**Files:**
- Create: `src/cardak/collectors/machine.py`
- Create: `tests/fixtures/proc/meminfo` (copy of a real `/proc/meminfo`; numbers are fine, nothing
  personal in it)
- Test: `tests/test_machine.py`
- Modify: `src/cardak/collectors/processes.py:1` docstring → "PsutilTable is the only per-process
  psutil and /proc reader."

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True)
  class MachineSample:
      mem_total: int        # bytes
      mem_available: int
      swap_total: int
      swap_free: int
      cpu_pct: float | None  # whole machine 0–100; None on the first sample
      n_cpus: int
      @property
      def mem_used(self) -> int: ...  # mem_total - mem_available

  def parse_meminfo(text: str) -> tuple[int, int, int, int]  # total, available, swap total, swap free (bytes)
  class MachineSampler:
      def __init__(self, meminfo: Path = Path("/proc/meminfo"), cpu: Callable[[], float] = ..., n_cpus: int | None = None) -> None
      def sample(self) -> MachineSample | None  # None when meminfo can't be read
  ```

- [ ] **Step 1: Write the failing tests**

```python
def test_parse_meminfo_fixture() -> None:
    total, avail, swap_total, swap_free = parse_meminfo((FIXTURES / "proc/meminfo").read_text())
    assert total > avail > 0 and swap_total >= swap_free >= 0
    assert total % 1024 == 0  # kB converted to bytes

def test_parse_meminfo_missing_field_raises() -> None:
    with pytest.raises(ValueError):
        parse_meminfo("MemTotal: 1000 kB\n")

def test_first_cpu_sample_is_none(tmp_path: Path) -> None:
    calls = iter([0.0, 37.5])
    s = MachineSampler(meminfo=FIXTURES / "proc/meminfo", cpu=lambda: next(calls), n_cpus=16)
    assert s.sample().cpu_pct is None
    second = s.sample()
    assert second.cpu_pct == 37.5 and second.n_cpus == 16
    assert second.mem_used == second.mem_total - second.mem_available

def test_unreadable_meminfo_is_none(tmp_path: Path) -> None:
    assert MachineSampler(meminfo=tmp_path / "nope").sample() is None
```

- [ ] **Step 2: Run** `uv run pytest tests/test_machine.py -v` — FAIL (module missing).
- [ ] **Step 3: Implement** `collectors/machine.py`. `cpu` defaults to
  `lambda: psutil.cpu_percent(None)` (its first call is meaningless, hence None);
  `n_cpus` defaults to `psutil.cpu_count() or 1`. `parse_meminfo` raises `ValueError` when
  any of `MemTotal`, `MemAvailable`, `SwapTotal`, `SwapFree` is missing.
- [ ] **Step 4: Run** `uv run pytest tests/test_machine.py -v` — PASS.
- [ ] **Step 5: Commit** `feat(collectors): machine memory and CPU (#57)`

---

### Task 2: Docker collector

Measured on the dev machine (cgroup v2, systemd driver):
- containers: `/sys/fs/cgroup/system.slice/docker-<full id>.scope/`
- BuildKit build steps: `/sys/fs/cgroup/system.slice/system.slice:docker:<build id>/`
  (the directory exists only while a step runs)
- both have `memory.current` (bytes) and `cpu.stat` with a `usage_usec <n>` line.
- `GET /containers/json` returns `Id`, `Names` (`["/name"]`), `Labels`, `Mounts`
  (`{"Type": "bind", "Source": "/abs/path", ...}`).

**Files:**
- Create: `src/cardak/collectors/docker.py`
- Create: `tests/fixtures/docker/containers.json` — record it: start two throwaway busybox
  containers, one with `--label com.docker.compose.project.working_dir=<a scratch dir>` and one
  with `-v <scratch dir>:/work`, plus one with neither; save `curl --unix-socket
  /var/run/docker.sock http://localhost/containers/json`; remove the containers and the image;
  then sanitize every path to `/home/user/repos/webshop` (compose) and
  `/home/user/repos/api-gw/data` (mount), and container names to `webshop-db-1`, `api-gw-cache`,
  `scratch`.
- Create: `tests/fixtures/docker/cgroup/` holding `docker-<id>.scope/{memory.current,cpu.stat}`
  for the three fixture ids and one `system.slice:docker:<build id>/{memory.current,cpu.stat}`.
- Test: `tests/test_docker.py`

**Interfaces:**
- Produces:
  ```python
  DOCKER_SOCK = Path("/var/run/docker.sock")
  class DockerUnavailable(Exception): ...

  @dataclass(frozen=True)
  class Container:
      id: str
      name: str                  # without the leading "/"
      compose_dir: Path | None   # com.docker.compose.project.working_dir
      mounts: tuple[Path, ...]   # bind mount sources only

  @dataclass(frozen=True)
  class DockerWork:
      kind: Literal["container", "build"]
      id: str
      name: str                  # container name, or "build" for a build step
      rss: int                   # memory.current
      cpu_pct: float | None      # None until a second sample of the same cgroup
      compose_dir: Path | None = None
      mounts: tuple[Path, ...] = ()

  def parse_containers(data: object) -> list[Container]
  def list_containers(sock: Path = DOCKER_SOCK, timeout: float = 0.5) -> list[Container]  # raises DockerUnavailable
  def read_cgroup(path: Path) -> tuple[int, int] | None  # (memory bytes, cpu usage_usec); None if gone/unreadable
  class DockerSampler:
      def __init__(self, list_fn: Callable[[], list[Container]] = list_containers,
                   cgroup_root: Path = Path("/sys/fs/cgroup/system.slice"),
                   clock: Callable[[], float] = time.monotonic) -> None
      def sample(self) -> list[DockerWork]
  ```

- [ ] **Step 1: Write the failing tests**

```python
def test_parse_containers_fixture() -> None:
    cs = {c.name: c for c in parse_containers(json.loads((FIX / "containers.json").read_text()))}
    assert cs["webshop-db-1"].compose_dir == Path("/home/user/repos/webshop")
    assert cs["api-gw-cache"].mounts == (Path("/home/user/repos/api-gw/data"),)
    assert cs["scratch"].compose_dir is None and cs["scratch"].mounts == ()

def test_parse_containers_ignores_volume_mounts_and_bad_entries() -> None:
    data = [{"Id": "a", "Names": ["/x"], "Labels": None,
             "Mounts": [{"Type": "volume", "Source": "/var/lib/docker/volumes/v"}]}, "junk"]
    assert parse_containers(data) == [Container("a", "x", None, ())]

def test_list_containers_unreachable(tmp_path: Path) -> None:
    with pytest.raises(DockerUnavailable):
        list_containers(tmp_path / "docker.sock")

def test_read_cgroup(tmp_path: Path) -> None:
    d = tmp_path / "g"; d.mkdir()
    (d / "memory.current").write_text("1048576\n")
    (d / "cpu.stat").write_text("usage_usec 2500000\nuser_usec 1\n")
    assert read_cgroup(d) == (1048576, 2500000)
    assert read_cgroup(tmp_path / "gone") is None

def test_first_container_cpu_is_none_then_delta(tmp_path: Path) -> None:
    # copy FIX/"cgroup" to tmp_path; clock 0.0 then 2.0; rewrite usage_usec +1_000_000 between
    # samples: the second sample's cpu_pct == 50.0 (1 s of CPU over 2 s wall)
    ...

def test_builds_are_found_without_the_api(tmp_path: Path) -> None:
    def down() -> list[Container]:
        raise DockerUnavailable("no socket")
    work = DockerSampler(list_fn=down, cgroup_root=FIX / "cgroup").sample()
    assert [w.kind for w in work] == ["build"]

def test_vanished_cgroup_is_skipped() -> None:
    gone = Container("deadbeef", "gone", None, ())  # no docker-deadbeef.scope in the fixture
    work = DockerSampler(list_fn=lambda: [gone], cgroup_root=FIX / "cgroup").sample()
    assert all(w.id != "deadbeef" for w in work)
    assert [w.kind for w in work] == ["build"]
```

(`test_sampler_without_docker_still_reads_builds` is the `test_builds_are_found_without_the_api`
case above; name it either way.)

- [ ] **Step 2: Run** `uv run pytest tests/test_docker.py -v` — FAIL.
- [ ] **Step 3: Implement** `collectors/docker.py`. `list_containers` subclasses
  `http.client.HTTPConnection` with an `AF_UNIX` `connect`, sends `GET /containers/json`, and
  turns `OSError`, a non-200 status and bad JSON into `DockerUnavailable`. It honours
  `DOCKER_HOST=unix://<path>` when `sock` is the default (read `os.environ` in the default
  argument path only). `DockerSampler.sample` keeps `{cgroup path: (usage_usec, clock)}` from the
  previous call to compute `cpu_pct = Δusage_usec / (Δclock × 1e6) × 100`, drops entries not
  seen this round, and catches `DockerUnavailable` (containers then empty, builds still read).
  Builds: every directory in `cgroup_root` whose name starts with `system.slice:docker:`.
- [ ] **Step 4: Run** `uv run pytest tests/test_docker.py -v` — PASS.
- [ ] **Step 5: Commit** `feat(collectors): Docker containers and builds (#57)`

---

### Task 3: Panes know about MCP and docker builds

**Files:**
- Modify: `src/cardak/collectors/processes.py` (`PaneProcs`, `_pane_procs`, new `is_docker_build`)
- Modify: `src/cardak/core/snapshot.py` (`PaneRow`, `_row`, `_group`)
- Test: `tests/test_processes.py`, `tests/test_snapshot.py`

**Interfaces:**
- Produces:
  - `is_docker_build(p: ProcInfo) -> bool` — true for argv words `docker build`,
    `docker buildx build`, `docker image build`, `docker builder build`, `docker compose build`
    (also `docker-compose build`); option words between them don't matter
    (`docker compose -f x.yml build`).
  - `PaneProcs.docker_build: bool = False` — any process in the pane's work tree matches.
  - `PaneRow.mcp_rss: int = 0`, `PaneRow.mcp_cpu_pct: float = 0.0`, `PaneRow.docker_build: bool = False`.
  - `Group.rss` / `Group.cpu_pct` now include their panes' MCP numbers (the project's real cost).
    `PaneRow.rss` / `cpu_pct` stay work-only.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("argv,expected", [
    (("docker", "build", "."), True),
    (("docker", "buildx", "build", "-t", "x", "."), True),
    (("docker", "compose", "-f", "c.yml", "build"), True),
    (("docker-compose", "build"), True),
    (("docker", "run", "busybox"), False),
    (("docker", "compose", "up"), False),
    (("make", "build"), False),
])
def test_is_docker_build(argv, expected) -> None:
    assert is_docker_build(proc(5, name=argv[0], cmdline=argv)) is expected

def test_pane_with_docker_build_client() -> None:
    procs = [proc(10, tty=T, name="zsh"), proc(11, 10, tty=T, cmdline=("docker", "build", "."))]
    assert summarize(procs, [T])[T].docker_build is True

def test_group_totals_include_mcp() -> None:
    # a pane with work rss 1 GiB and mcp_rss 0.5 GiB: row.rss == 1 GiB, row.mcp_rss == 0.5 GiB,
    # group.rss == 1.5 GiB
    ...
```

- [ ] **Step 2: Run** `uv run pytest tests/test_processes.py tests/test_snapshot.py -v` — new tests FAIL.
- [ ] **Step 3: Implement** the fields; `_row` copies `mcp_rss`, `mcp_cpu_pct`, `docker_build`
  from `PaneProcs`; `_group` sums `r.rss + r.mcp_rss` and `(r.cpu_pct or 0) + r.mcp_cpu_pct`.
- [ ] **Step 4: Run** `uv run pytest -q` — all PASS (existing group-total tests may need their
  expected numbers updated only where a fixture pane has MCP children).
- [ ] **Step 5: Commit** `feat(core): count MCP servers in project totals; spot docker builds (#57)`

---

### Task 4: `[resources]` settings and thresholds

**Files:**
- Modify: `src/cardak/core/config.py` (new `ResourceSettings`, `Config.resources`, `_resources`)
- Create: `src/cardak/core/resources.py`
- Test: `tests/test_config.py`, `tests/test_resources.py`

**Interfaces:**
- Produces:
  ```python
  # config.py — every field None means "derive from the machine"
  @dataclass(frozen=True)
  class ResourceSettings:
      ram_warn_available_gb: float | None = None
      ram_critical_available_gb: float | None = None
      pane_ram_gb: float | None = None
      growth_gb_per_min: float | None = None
      pane_cpu_pct: float | None = None
      machine_cpu_pct: float | None = None
      sustain_s: float | None = None
      repeat_min: float | None = None
  Config.resources: ResourceSettings = ResourceSettings()

  # resources.py
  GIB = 2**30
  @dataclass(frozen=True)
  class Thresholds:
      ram_warn_available: int      # bytes
      ram_critical_available: int
      pane_ram: int
      growth_per_min: int
      pane_cpu_pct: float          # deck percent, 100 = one core
      machine_cpu_pct: float       # 0–100
      sustain_s: float
      repeat_s: float
  MemoryLevel = Literal["warning", "critical"]
  def derive(settings: ResourceSettings, mem_total: int, n_cpus: int) -> Thresholds
  def memory_level(m: MachineSample, t: Thresholds) -> MemoryLevel | None
  def is_over(rss: int, cpu_pct: float | None, t: Thresholds) -> bool
  ```

- [ ] **Step 1: Write the failing tests**

```python
def test_defaults_for_a_16_core_32_gib_machine() -> None:
    t = derive(ResourceSettings(), 32 * GIB, 16)
    assert t.ram_warn_available == int(0.15 * 32 * GIB)
    assert t.ram_critical_available == int(0.07 * 32 * GIB)
    assert t.pane_ram == 8 * GIB
    assert t.growth_per_min == GIB
    assert t.pane_cpu_pct == 800.0 and t.machine_cpu_pct == 90.0
    assert t.sustain_s == 120.0 and t.repeat_s == 600.0

def test_settings_override_defaults() -> None:
    t = derive(ResourceSettings(pane_ram_gb=4.0, repeat_min=1), 32 * GIB, 16)
    assert t.pane_ram == 4 * GIB and t.repeat_s == 60.0

def test_memory_level() -> None:
    t = derive(ResourceSettings(), 32 * GIB, 16)
    # available 10 GiB → None; 4 GiB → "warning"; 2 GiB → "critical";
    # 10 GiB available but swap_total > 0 and swap_free == 0 → "critical";
    # swap_total == 0 (no swap) and swap_free == 0 → not critical on that ground

def test_is_over() -> None:
    t = derive(ResourceSettings(), 32 * GIB, 16)
    assert is_over(9 * GIB, 10.0, t) and is_over(0, 801.0, t)
    assert not is_over(7 * GIB, None, t)

# test_config.py
def test_resources_section_parsed(tmp_path) -> None:  # pane_ram_gb = 10.5, sustain_s = 60 → those values; rest None
def test_resources_rejects_bad_values(tmp_path) -> None:
    # "[resources]\npane_ram_gb = -1" → ConfigError "[resources] pane_ram_gb must be a number > 0"
    # machine_cpu_pct = 150 → ConfigError "... machine_cpu_pct must be a number > 0 and ≤ 100"
    # pane_ram_gb = true → ConfigError (bool is not a number)
    # resources = 3 (not a table) → ConfigError "[resources] must be a table"
```

- [ ] **Step 2: Run** `uv run pytest tests/test_resources.py tests/test_config.py -v` — FAIL.
- [ ] **Step 3: Implement.** `_resources` follows `_summary`'s style; unknown keys are ignored like
  the other sections. `config.py` keeps its import rule (no new imports at module level).
  `resources.py` imports `MachineSample` from `cardak.collectors.machine` and `ResourceSettings`
  from `cardak.core.config`.
- [ ] **Step 4: Run** `uv run pytest -q` — PASS, including `tests/test_cli.py` import guards.
- [ ] **Step 5: Commit** `feat(core): resource thresholds from the machine and [resources] (#57)`

---

### Task 5: Accounting — Docker to projects, Other as the remainder

**Files:**
- Create: `src/cardak/core/accounting.py`
- Modify: `src/cardak/core/snapshot.py` (`Group` fields, `DeckSnapshot.machine`, public `group_for`)
- Test: `tests/test_accounting.py`

**Interfaces:**
- Consumes: `DockerWork` (Task 2), `MachineSample` (Task 1), `PaneRow.docker_build` (Task 3).
- Produces:
  ```python
  # snapshot.py
  @dataclass(frozen=True)
  class DockerLine:
      label: str           # "docker: webshop-db-1", "docker build", "docker builds, 2 panes"
      rss: int
      cpu_pct: float | None
  Group.docker: tuple[DockerLine, ...] = ()
  Group.outside_rss: int | None = None      # only on the Other group: remainder minus its panes
  Group.outside_cpu_pct: float | None = None
  DeckSnapshot.machine: MachineSample | None = None
  def group_for(resolved: Resolved, home: Path, accent_for: AccentFor | None = None) -> Group  # a group with no panes

  # accounting.py
  def match_project(work: DockerWork, roots: Sequence[Path]) -> Path | None
  def account(snap: DeckSnapshot, work: Sequence[DockerWork], machine: MachineSample | None,
              roots: Sequence[Path], make_group: Callable[[Path], Group]) -> DeckSnapshot
  ```

Rules (spec "Accounting"):
- `match_project`: `compose_dir` first; else the first bind mount that lies inside a root.
  A path matches a root when it equals or is inside it; among matching roots the deepest wins.
  A mount *above* a root (e.g. the home dir) matches nothing.
- Containers matched to a root whose group is in `snap.groups` add a `DockerLine` to it, and their
  `rss`/`cpu_pct` to the group's totals. A matched root with no group gets `make_group(root)`,
  inserted after the existing project groups and before Other (a project running only Docker still
  shows).
- Builds: let P = the project groups (kind `config`/`git`) with any `row.docker_build`. If
  `len(P) == 1`, all build work becomes one line `docker build` (summed) on that group. Otherwise
  the builds go on the Other group as `docker builds, N panes` (N = building panes across all
  groups; `docker build` when N is 0 or 1).
- Unmatched containers go on the Other group as lines, **not** added to its totals.
- With `machine`: Other's `rss = max(0, machine.mem_used − Σ project group rss)`, `cpu_pct =
  max(0, machine.cpu_pct × n_cpus − Σ project group cpu)` (0 when `machine.cpu_pct` is None);
  `outside_rss = max(0, Other.rss − Σ Other's pane rss + mcp)`, same for CPU. If there is no
  Other group, append `make_group`-style empty Other (use `group_for(OTHER, ...)`). Without
  `machine`, Other keeps its pane sums and `outside_*` stay None.
- The waiting group is left as it is; `snap.machine = machine`.

- [ ] **Step 1: Write the failing tests**

```python
ROOTS = [Path("/home/user/repos/webshop"), Path("/home/user/repos/api-gw"),
         Path("/home/user/repos/api-gw/vendor/lib")]

def test_mount_matching() -> None:
    assert match_project(c(compose="/home/user/repos/webshop"), ROOTS) == ROOTS[0]
    assert match_project(c(mounts=["/home/user/repos/api-gw/data"]), ROOTS) == ROOTS[1]
    assert match_project(c(mounts=["/home/user/repos/api-gw/vendor/lib/x"]), ROOTS) == ROOTS[2]
    assert match_project(c(mounts=["/home/user"]), ROOTS) is None
    assert match_project(c(), ROOTS) is None

def test_container_adds_to_its_project() -> None:
    # webshop group with a 1 GiB pane + container 2 GiB/30% matched by compose →
    # group.rss == 3 GiB, group.cpu_pct == pane cpu + 30, group.docker == (DockerLine("docker: webshop-db-1", 2 GiB, 30.0),)

def test_project_with_only_docker_gets_a_group() -> None:
    # container matched to api-gw, no api-gw panes → a group from make_group(api-gw root) before Other

def test_one_building_project_owns_the_builds() -> None:
    # two build DockerWork (1 GiB, 0.5 GiB), one webshop pane with docker_build=True →
    # webshop.docker == (DockerLine("docker build", 1.5 GiB, ...),)

def test_builds_in_two_projects_go_to_other() -> None:
    # webshop and api-gw each have a building pane → Other.docker has "docker builds, 2 panes";
    # neither project's totals change

def test_other_is_the_remainder() -> None:
    # machine used 20 GiB, projects 6 GiB, Other panes 1 GiB → Other.rss == 14 GiB,
    # Other.outside_rss == 13 GiB; CPU: machine 25% × 16 = 400, projects 150, Other panes 50 →
    # Other.cpu_pct == 250, outside_cpu_pct == 200

def test_other_clamped_at_zero() -> None:
    # machine used 4 GiB, projects 6 GiB → Other.rss == 0 and outside_rss == 0

def test_other_appears_without_unassigned_panes() -> None:
    # all panes in projects, machine known → last group is kind "other" with no panes

def test_no_machine_leaves_other_alone() -> None:
    # machine None → Other.rss == Σ its panes, outside_rss is None
```

- [ ] **Step 2: Run** `uv run pytest tests/test_accounting.py -v` — FAIL.
- [ ] **Step 3: Implement** `accounting.py` (pure, no I/O) and the snapshot fields. Keep
  `account` a short coordinator over small helpers (`_attach_containers`, `_attach_builds`,
  `_remainder`) so enola's complexity detector stays quiet.
- [ ] **Step 4: Run** `uv run pytest -q` — PASS.
- [ ] **Step 5: Commit** `feat(core): attribute Docker to projects and show Other as the remainder (#57)`

---

### Task 6: Recent history, sparklines and "over" marks

**Files:**
- Create: `src/cardak/core/recent.py`
- Modify: `src/cardak/core/snapshot.py` (sparkline/over fields)
- Test: `tests/test_recent.py`

**Interfaces:**
- Consumes: `Thresholds`, `is_over`, `memory_level` (Task 4); snapshot fields (Task 5).
- Produces:
  ```python
  # snapshot.py
  PaneRow.ram_spark: str = ""
  PaneRow.over: bool = False
  Group.ram_spark: str = ""
  Group.over: bool = False
  @dataclass(frozen=True)
  class MachineView:
      sample: MachineSample
      level: MemoryLevel | None
      ram_spark: str
      cpu_spark: str
  DeckSnapshot.machine_view: MachineView | None = None

  # recent.py
  BARS = "▁▂▃▄▅▆▇█"
  def spark(values: Sequence[float | None], top: float, width: int = 8) -> str
  class Recent:
      def __init__(self, keep: int = 120) -> None   # 120 polls ≈ 4 min at 2 s
      def record(self, snap: DeckSnapshot) -> None    # groups "g:<kind>:<key>", panes "p:<pane_id>", "machine"; drops keys not in snap
      def ram(self, key: str) -> list[float]
      def cpu(self, key: str) -> list[float | None]
  def decorate(snap: DeckSnapshot, recent: Recent, t: Thresholds) -> DeckSnapshot
  ```

Rules:
- `spark`: the last `width` buckets of the series, right-aligned (newest on the right), each
  bucket the max of `ceil(len/width)` consecutive values; level `min(7, int(v / top * 8))`; an
  all-None bucket is a space; fewer values than `width` gives a shorter string. `top <= 0` → "".
- Tops: pane and project RAM → `t.pane_ram`; machine RAM → `mem_total`; machine CPU → 100.
- `over`: panes `is_over(rss + mcp_rss, cpu + mcp_cpu, t)`; groups `is_over(g.rss, g.cpu_pct, t)`
  except kind `other` and `waiting` (never). Rows inside the waiting group get the same
  `ram_spark`/`over` as the same pane in its project (look up by `pane_id`).
- `machine_view` is None when `snap.machine` is None.

- [ ] **Step 1: Write the failing tests**

```python
def test_spark_scales_to_top() -> None:
    assert spark([0, 4, 8], top=8, width=8) == "▁▅█"
    assert spark([None, None], top=8) == " "
    assert spark([], top=8) == ""
    assert spark([1.0], top=0) == ""

def test_spark_buckets_take_the_max() -> None:
    assert spark([1, 8, 1, 1], top=8, width=2) == "█▂"

def test_recent_keeps_a_window_and_forgets_closed_panes() -> None:
    # keep=3, record 5 snapshots with pane 1 → ram("p:1") has 3 values;
    # a snapshot without pane 1 → ram("p:1") == []

def test_decorate_marks_over_and_spares_other() -> None:
    # 32 GiB/16 cores thresholds: pane at 9 GiB → over; project containing it → over;
    # Other at 14 GiB → not over; waiting-group copy of the 9 GiB pane → over

def test_machine_view_levels() -> None:
    # machine with 4 GiB available of 32 → machine_view.level == "warning"; None machine → None view
```

- [ ] **Step 2: Run** `uv run pytest tests/test_recent.py -v` — FAIL.
- [ ] **Step 3: Implement** `recent.py`.
- [ ] **Step 4: Run** `uv run pytest -q` — PASS.
- [ ] **Step 5: Commit** `feat(core): short usage history, sparklines and threshold marks (#57)`

---

### Task 7: Wire it into LiveSource

**Files:**
- Modify: `src/cardak/core/source.py`
- Test: `tests/test_source.py`

**Interfaces:**
- Consumes: `MachineSampler.sample`, `DockerSampler.sample`, `account`, `derive`, `Recent`,
  `decorate`, `group_for`.
- Produces: `LiveSource.__init__` gains
  `machine: Callable[[], MachineSample | None] | None = None` (default `MachineSampler().sample`)
  and `docker: Callable[[], Sequence[DockerWork]] | None = None` (default `DockerSampler().sample`).
  `cli.py` needs no change.

Rules:
- `_poll`: after `build_snapshot`, a new `_resources(snap) -> DeckSnapshot` runs
  sample machine → sample docker → `account` (roots = config project paths + keys of `git`
  groups in the snapshot; `make_group = lambda root: group_for(self._resolver.resolve(root),
  self._home, self._accent_for)`) → `derive(self._config.resources, ...)` (only when the machine
  sample exists; else skip marks) → `self._recent.record` → `decorate`.
- When WezTerm is unreachable, the error snapshot still carries `machine` and `machine_view`
  (the header keeps working); no accounting.
- An exception from the machine or Docker sampler never breaks the poll: treat it as
  None / `[]`.

- [ ] **Step 1: Write the failing tests** (fakes for `machine` and `docker`, like the existing
  `list_panes`/`sample` fakes in `tests/test_source.py`):

```python
def test_poll_attaches_docker_and_machine(...) -> None:
    # one webshop pane, a container with compose_dir = the webshop root, machine 20 GiB used →
    # webshop group has the docker line; last group is Other with outside_rss set;
    # snap.machine_view is not None

def test_unreachable_wezterm_still_shows_machine(...) -> None:
    # list_panes raises WeztermUnavailable → snap.error set and snap.machine_view not None

def test_sampler_errors_do_not_break_poll(...) -> None:
    # machine raises OSError, docker raises RuntimeError → poll returns groups, machine_view None

def test_resources_setting_changes_marks(...) -> None:
    # projects.toml with [resources] pane_ram_gb = 0.5 and a 1 GiB pane → that row is over
```

- [ ] **Step 2: Run** `uv run pytest tests/test_source.py -v` — new tests FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest -q` — PASS.
- [ ] **Step 5: Commit** `feat(core): live resources in the deck's snapshot (#57)`

---

### Task 8: Show it in the deck

**Files:**
- Modify: `src/cardak/ui/widgets.py`, `src/cardak/ui/app.py`
- Test: `tests/test_widgets.py`, `tests/test_app.py`

**Interfaces:**
- Consumes: the snapshot fields from Tasks 3–6.
- Produces (widgets.py):
  ```python
  def usage(rss: int, cpu_pct: float | None, spark: str = "", over: bool = False) -> Text
      # "RAM 13.9G ▁▂▄▆█  CPU 180%"; over → RAM and CPU bold red
  def mcp_label(r: PaneRow) -> Text            # "    └ MCP servers  RAM 412M  CPU 1%", dim
  def docker_label(line: DockerLine) -> Text   # "    docker: webshop-db-1  RAM 2.0G  CPU 30%"
  def outside_label(g: Group) -> Text          # "    outside WezTerm  RAM 13.0G  CPU 200%", dim
  def machine_label(v: MachineView) -> Text
      # "CPU 38% ▂▃▃▅▄▃   RAM 27.6 / 32.0G ▃▄▅▆▇█" then, by level:
      # "  ⚠ 14% free" (warning, $warning colour) / "  ⚠ 6% free" bold red (critical),
      # "  swap full" appended when swap_total > 0 and swap_free == 0
  ```
  `group_label` and `pane_label` use `usage(...)` in place of today's `CPU …  RAM …` (RAM first,
  then sparkline, then CPU); `pane_label` puts `▲ ` before the label when `r.over`.
- app.py:
  - `Node.role` adds `"mcp"`, `"docker"`, `"outside"`; `Node` gains `line: DockerLine | None = None`.
  - `node_id` checks the role **before** `row is None`: `m:<pane_id>`, `d:<group kind>:<group key>:<label>`,
    `o:<group key>` — so these lines never collide with their group's id.
  - `_leaves`: an `mcp` line after a pane when `row.mcp_rss > 0`; after the panes, one `docker`
    line per `group.docker`, then an `outside` line when `group.outside_rss is not None`.
    Not under the waiting group.
  - A `Static(id="machine")` between `Header` and `#banner`, updated in `apply_snapshot` from
    `snap.machine_view`, hidden when None.
  - Enter on an `mcp` line jumps to its pane; on `docker`/`outside` lines it does nothing.

- [ ] **Step 1: Write the failing tests**

```python
def test_group_labels() -> None:  # updated
    assert group_label(group()).plain == "webshop  ~/r/w  RAM 1.2G  CPU 12%"

def test_usage_with_spark_and_over() -> None:
    t = usage(int(13.9 * 2**30), 180.0, "▁▂▄▆█", over=True)
    assert t.plain == "RAM 13.9G ▁▂▄▆█  CPU 180%"
    assert any("red" in str(s.style) for s in t.spans)

def test_pane_over_gets_a_marker() -> None:
    assert pane_label(row(over=True)).plain.startswith("▲ ")

def test_machine_label_levels() -> None:
    # 27.6 of 32 GiB used, 4.4 GiB available, level "warning", cpu 38.0 →
    # plain == "CPU 38% <cpu spark>   RAM 27.6 / 32.0G <ram spark>  ⚠ 14% free"
    # cpu_pct None → "CPU –"; swap full → endswith "swap full"

# test_app.py (Textual pilot, as the existing tests do)
async def test_tree_shows_mcp_docker_and_outside_lines() -> None:
    # snapshot: webshop pane with mcp_rss, webshop docker line, Other with outside_rss →
    # tree lines include "└ MCP servers", "docker: webshop-db-1", "outside WezTerm"

async def test_cursor_on_docker_line_survives_refresh() -> None:
    # move the cursor to the docker line, apply an equal-shaped snapshot with new numbers →
    # cursor still on the docker line (ids don't collide with the group)

async def test_machine_header_hidden_without_machine() -> None:
```

- [ ] **Step 2: Run** `uv run pytest tests/test_widgets.py tests/test_app.py -v` — new/updated tests FAIL.
- [ ] **Step 3: Implement.** Keep `_leaves` small (one helper per extra line kind if it grows past
  the complexity of today's version).
- [ ] **Step 4: Run** `uv run pytest -q` — PASS.
- [ ] **Step 5: Commit** `feat(ui): RAM, sparklines, MCP and Docker lines, machine header (#57)`

---

### Task 9: Docs, checks and a live look

**Files:**
- Modify: `AGENTS.md` (Layout: `collectors/{machine, docker}.py`, `core/{accounting, resources, recent}.py`;
  a "Resources (M5)" section with the measured Docker cgroup paths and the `/containers/json`
  fields used, the GiB rule, "machine used = MemTotal − MemAvailable", and that Docker access
  lives only in `collectors/docker.py`)
- Modify: `CHANGELOG.md` under `## Unreleased` → `### Added`:
  - `Every project and pane shows its RAM with a short graph of the last few minutes, and the machine's CPU and RAM sit at the top, with a warning when memory runs low. (#33, #57)`
  - `Docker containers count toward the project they belong to (by their Compose folder or a mounted project folder), and a docker build toward the project that started it. MCP servers under Claude show as their own line. Everything else is "Other", so the numbers add up to the machine. (#57)`
- Modify: `README.md` — a short "Resources" paragraph: what the numbers mean, that Docker needs
  the `docker` group, and the `[resources]` keys (written by `cardak resources install` in a later
  release; set them by hand now if you want).

- [ ] **Step 1:** `uv run ruff check && uv run ruff format --check && uv run pyright && uv run pytest --cov --cov-branch --cov-fail-under=80`
  — all green.
- [ ] **Step 2:** `uv run python -X importtime -c "import cardak.cli" 2>&1 | grep -E "docker|machine|accounting|recent"`
  — no output (none of the new modules load on `cardak hook`'s path).
- [ ] **Step 3:** `enola check` (baseline pinned on 0.5.0). Expected: no new cycle, no layer
  crossing. New complexity/hotspot findings: split them if they are real outliers by absolute size;
  record accepted coordinators in AGENTS.md "Architecture checks" (judge per that section).
- [ ] **Step 4: Live look**, isolated per AGENTS.md: a test WezTerm (`--always-new-process`,
  scratch `XDG_*`), the deck in it via `env` with the scratch dirs. Check the header, a project with
  a `claude` pane (MCP line), a throwaway busybox container with a bind mount into a scratch
  project folder (docker line on that project), and Other's "outside WezTerm" line. Remove the
  container and image afterwards. Capture by `_NET_WM_PID`; never change focus.
- [ ] **Step 5: Commit** `docs: resources in AGENTS.md, README and the changelog (#57)`
