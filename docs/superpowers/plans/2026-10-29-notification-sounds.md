# Per-theme notification sounds Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Waiting, memory and other notifications each play their own sound, chosen as random from a theme pool, off, or one named sound, with local openpeon packs as the source of the fun clips.

**Architecture:** A new collector, `collectors/soundpacks.py`, parses `openpeon.json` manifests into clips. `core/config.NotifySettings` gains three theme keys and resolves the legacy `sound`. `core/alerts` builds a pool per theme and picks from it. The hook plays the waiting theme. The daemon maps each alert kind to memory or other.

**Tech Stack:** Python 3.13, stdlib `json`, pytest; `pw-play`/`paplay` as today.

**Spec:** `docs/superpowers/specs/2026-10-29-notification-sounds-design.md`

## Global Constraints

- Packs live in `src/cardak/sounds/packs/<folder>/` (`openpeon.json` + files). The folder is git-ignored and excluded from the wheel and sdist, and no pack file is ever committed.
- `collectors/soundpacks.py` imports nothing from cardak (AGENTS.md layering).
- Accepted extensions: `.wav`, `.oga`, `.ogg`, `.mp3`, compared case-insensitively.
- A pack clip's name is `<folder>/<file stem>`. Any listed clip is nameable. Random uses `input.required` for waiting and `resource.limit` for memory.
- Fallbacks: waiting with no `input.required` clip uses today's pool (bundled + `sounds_dir`); memory with no `resource.limit` clip uses `bell`; other always uses `chime`. A pool is never empty unless the setting is `"off"`.
- Tests never read the real `src/cardak/sounds/packs/`. An autouse fixture in `tests/conftest.py` points `cardak.core.alerts.pack_root` at an empty tmp dir.
- `cardak hook` budget < 150 ms: packs are read only on the ping path (inside `alert`), and the `tests/test_cli.py` import guards still pass.
- Commit trailers per the session; scopes `feat(collectors)`, `feat(core)`, `docs`; subjects end `(#91)`.

## Review Focus

1. A manifest `file` that escapes its pack (`../x.wav`, an absolute path): skipped, never played (Task 1 test `test_paths_outside_the_pack_are_skipped`).
2. A stray file or folder in `packs/` (a `README`, a folder with no manifest, broken JSON): the pack is skipped with a problem line, and other packs still load (Task 1 `test_broken_packs_are_reported_not_fatal`).
3. A legacy `sound = "off"` together with one theme key set: only the keys left unset follow `sound` (Task 2 `test_legacy_off_with_one_theme_key`).
4. The same file listed under two categories: nameable once, and in each pool once (Task 1 `test_clip_in_two_categories`).
5. A named sound that matches nothing: that theme plays its random pick and the deck warns, naming the theme's key (Task 3 `test_check_names_the_theme_key`).

---

### Task 1: The pack collector

**Files:**
- Create: `src/cardak/collectors/soundpacks.py`
- Create: `tests/fixtures/soundpacks/demo/openpeon.json` (two categories, neutral labels, `sha256` fields kept for shape)
- Test: `tests/test_soundpacks.py` (copies the fixture to `tmp_path` and writes empty clip files)

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Clip: name: str  # "<folder>/<stem>"; path: Path; categories: frozenset[str]`
  - `@dataclass(frozen=True) class Packs: clips: tuple[Clip, ...]; problems: tuple[str, ...]`
  - `read_packs(root: Path, exts: tuple[str, ...]) -> Packs`: folders in sorted order and clips in manifest order. A missing `root` gives `Packs((), ())`.

- [ ] **Step 1: Write the failing tests**

  - `test_reads_clips`: `demo` with `input.required: [sounds/A.wav]` and `resource.limit: [sounds/B.mp3]`. Assert the names are `["demo/A", "demo/B"]` and the categories are `{"input.required"}` and `{"resource.limit"}`.
  - `test_clip_in_two_categories`: the same file listed twice. Assert one Clip with both categories.
  - `test_missing_and_unsupported_files_are_skipped`: a listed file that doesn't exist and a `.txt` file. Assert neither appears and `problems == ()`. Missing clips are skipped silently.
  - `test_paths_outside_the_pack_are_skipped`: `../escape.wav` exists one level up and `/etc/hostname` is listed. Assert neither clip appears.
  - `test_broken_packs_are_reported_not_fatal`: one folder without a manifest, one with `{` as JSON, one where `categories` is a list, a plain file `README` in root, plus a good pack. Assert the good pack's clips load. Assert `problems` has one line per broken folder naming the folder; the folder with no manifest and `README` are silent.
  - `test_uppercase_extension`: `sounds/C.WAV` loads.
  - `test_missing_root`: `read_packs(tmp_path / "none", EXTS) == Packs((), ())`.

- [ ] **Step 2: Run** `uv run pytest tests/test_soundpacks.py -q`. Expected: FAIL, ModuleNotFoundError.
- [ ] **Step 3: Implement** `read_packs`. A clip's path is resolved, and it must satisfy `is_relative_to(pack_dir.resolve())` and `is_file()`. Bad shapes (a non-dict, a non-list `sounds`, a non-string `file`) skip that entry.
- [ ] **Step 4: Run** the tests. Expected: PASS. Run `uv run ruff check src tests && uv run pyright` as well.
- [ ] **Step 5: Commit** `feat(collectors): read openpeon sound packs (#91)`

### Task 2: Theme settings

**Files:**
- Modify: `src/cardak/core/config.py` (`NotifySettings`, `_notify`)
- Test: `tests/test_config.py`

**Interfaces:**
- Produces:
  - `Theme = Literal["waiting", "memory", "other"]` and `THEMES: tuple[Theme, ...]` in `core/config.py`.
  - `NotifySettings` fields `waiting_sound: str | None = None`, `memory_sound: str | None = None`, `other_sound: str | None = None`, added after `sounds_dir` so existing positional constructions still work.
  - `NotifySettings.theme_sound(theme: Theme) -> str`, which returns `"random"`, `"off"` or a name.
  - `NotifySettings.theme_key(theme: Theme) -> str`: `"waiting_sound"`, or `"sound"` when waiting is unset and `sound` was given.

- [ ] **Step 1: Write the failing tests**

  - `test_theme_keys_parsed`: the TOML sets all three keys. Assert each `theme_sound` value.
  - `test_theme_defaults`: `NotifySettings()` gives `random` for all three.
  - `test_legacy_sound_means_waiting`: `sound="bell"` makes waiting `"bell"` and leaves memory and other `"random"`.
  - `test_legacy_off_silences_all`: `sound="off"` and no theme keys make all three `"off"`.
  - `test_legacy_off_with_one_theme_key`: `sound="off"` and `memory_sound="peon/X"` give waiting `"off"`, memory `"peon/X"` and other `"random"`.
  - Extend `test_notify_bad_types` with `"[notify]\nmemory_sound = 2\n"`.

- [ ] **Step 2: Run** `uv run pytest tests/test_config.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement**:
  - waiting: `waiting_sound` if set, else `sound`;
  - memory and other: their own key if set, else `"off"` when `sound == "off"` and no theme key is set, else `"random"`.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat(core): a sound setting per notification theme (#91)`

### Task 3: Pools and picking per theme

**Files:**
- Modify: `src/cardak/core/alerts.py`, `src/cardak/core/source.py` (the `check` call), `tests/conftest.py` (autouse `pack_root` fixture)
- Test: `tests/test_alerts.py`, `tests/test_source.py`

**Interfaces:**
- Consumes: `read_packs`, `Clip`, `Packs` (Task 1); `Theme`, `theme_sound`, `theme_key` (Task 2).
- Produces:
  - `SOUND_EXTS = (".wav", ".oga", ".ogg", ".mp3")`;
  - `pack_root() -> Path`, which is `files("cardak.sounds")/"packs"` as a Path;
  - `named_sounds(settings, bundled, packs: Packs) -> dict[str, Path]`, merging bundled, then `sounds_dir`, then clips;
  - `theme_pool(theme: Theme, settings, bundled, packs) -> list[Path]`;
  - `choose_sound(theme: Theme, settings, bundled, packs, rng) -> Path | None`;
  - `check(settings, bundled, packs) -> list[str]`;
  - `resource_alert(level, title, body, settings, *, theme: Theme, spawn=..., rng=..., which=...)`;
  - `alert(...)`, with an unchanged signature, always plays `"waiting"`.
  - `sound_pool` is removed. Its only callers are `choose_sound`, `check` and the tests.

- [ ] **Step 1: Write the failing tests**, with `BUNDLED` gaining `"pluck"` and a tmp `Packs` built from `Clip`s directly:
  - `test_waiting_pool_with_packs`: one `input.required` clip plus a `sounds_dir` file. The pool is exactly those two, with no bundled sounds.
  - `test_waiting_pool_without_packs`: `Packs((), ())` gives bundled + `sounds_dir`, as today.
  - `test_memory_pool`: `resource.limit` clips. Without them the pool is `[BUNDLED["bell"]]`.
  - `test_other_pool_is_chime`: `[BUNDLED["chime"]]`, even with packs.
  - `test_choose_named_pack_clip`: `waiting_sound="demo/A"` picks that clip, even when its category is `task.complete`.
  - `test_choose_off_and_unknown`: `"off"` gives None; an unknown name falls back to a pick from that theme's pool.
  - `test_check_names_the_theme_key`: `memory_sound="nope"` warns `'[notify] memory_sound "nope" not found; playing a random one'`. Legacy `sound="nope"` warns with `sound`, as today. Pack problems are appended as `f"sound pack: {p}"`.
  - `test_resource_alert_plays_its_theme`: `resource_alert(..., theme="other")` with packs passes `BUNDLED["chime"]` to the player argv. Inject packs through the monkeypatched `pack_root` with a written fixture pack.
  - `test_mp3_in_sounds_dir`: an `x.mp3` in `sounds_dir` joins the waiting pool.
  - Update the existing `choose_sound`, `check` and `sound_pool` tests to the new signatures.
- [ ] **Step 2: Run** `uv run pytest tests/test_alerts.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - `_play(theme, settings, ...)` calls `read_packs(pack_root(), SOUND_EXTS)` once per play.
  - In `LiveSource._reload_if_changed`, pass `read_packs(pack_root(), SOUND_EXTS)` to `check`.
  - Add the autouse fixture in `conftest.py`, so `monkeypatch.setattr("cardak.core.alerts.pack_root", lambda: tmp_path / "no-packs")`.
- [ ] **Step 4: Run** `uv run pytest -q` (whole suite: the daemon still calls `resource_alert` without `theme` until Task 4, so give `theme` the default `"other"` here and remove that default in Task 4). Expected: PASS. Run `uv run pyright` too.
- [ ] **Step 5: Commit** `feat(core): pick notification sounds per theme from local packs (#91)`

### Task 4: The daemon plays memory or other

**Files:**
- Modify: `src/cardak/core/daemon.py` (`Notify`, `_notify`, the two `self.notify` calls), `src/cardak/core/alerts.py` (drop the `theme` default)
- Test: `tests/test_daemon.py`

**Interfaces:**
- Consumes: `Theme`; `resource_alert(..., theme=)`.
- Produces:
  - `THEME_BY_KIND: Mapping[str, Theme]` in `core/daemon.py`: `memory`, `big`, `growth` → `"memory"`; `busy`, `machine_busy` → `"other"`. The crash report always uses `"memory"`, and an unknown kind uses `"other"`.
  - `Notify = Callable[[str, str, str, NotifySettings, Theme], None]`.

- [ ] **Step 1: Write the failing tests**:
  - `test_memory_alerts_play_the_memory_theme`: the recorded notify call's last element is `"memory"`, reusing the existing memory-critical setup.
  - `test_cpu_alerts_play_other`.
  - `test_crash_report_plays_memory`: extend the existing loud-crash test.
- [ ] **Step 2: Run** `uv run pytest tests/test_daemon.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** `THEME_BY_KIND.get(a.kind, "other")`.
- [ ] **Step 4: Run** `uv run pytest -q && uv run pyright`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(core): memory alerts and crash reports use the memory sounds (#91)`

### Task 5: Packaging, the user's packs, docs

**Files:**
- Modify: `.gitignore` (`src/cardak/sounds/packs/`), `pyproject.toml` (`[tool.uv.build-backend]` `source-exclude` and `wheel-exclude` with `"cardak/sounds/packs/**"`, or whatever `uv build` accepts), `README.md` (`[notify]` example), `AGENTS.md` (Layout line for `collectors/soundpacks.py`; a short "### Notification sounds (#91)" section under Agent status; the "One interface per external format" line gains openpeon manifests), `CHANGELOG.md` (one Added line, #91)
- Test: `tests/test_packaging.py`

- [ ] **Step 1: Write the failing test** `test_packs_are_ignored`. It reads `.gitignore` from the repo root and asserts `"src/cardak/sounds/packs/"` is among its lines.
- [ ] **Step 2: Run** it. Expected: FAIL.
- [ ] **Step 3: Make the edits.**
  - Add the ignore line and the build excludes.
  - Copy the user's packs (from the folder they named in the session) into `src/cardak/sounds/packs/`.
  - Write the docs:
    - README `[notify]`: the three keys, the `<folder>/<stem>` names, and the packs folder;
    - AGENTS: where packs go, git-ignored and excluded from builds, the theme mapping, and the fallbacks.
- [ ] **Step 4: Verify**:
  - `uv run pytest -q`: PASS.
  - `git status --short`: shows no file under `packs/`.
  - `git check-ignore -q src/cardak/sounds/packs/peon/openpeon.json`: exit 0.
  - `uv build -o <scratch>`, then `unzip -l` on the wheel and `tar tzf` on the sdist: no `packs/`.
  - A live pick with no sound: `uv run python -c` calls `alerts.choose_sound` for each theme with the real `pack_root()` and prints the paths. Expected: waiting is an `input.required` clip from any of the five packs, memory a `resource.limit` clip, and other `chime.wav`.
- [ ] **Step 5: Commit** `docs: per-theme notification sounds (#91)` (with `.gitignore`, `pyproject.toml` and the test). Before committing, grep the diff for home paths and other-repo names.
