# M1 Read-only Deck Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `cardak` opens a Textual TUI that shows every WezTerm pane grouped by project, with live CPU/RAM per pane's process tree, refreshing every 2s, plus "add as project".

**Architecture:** Collectors (`wezterm`, `processes`, `claude`) are the only code touching external formats. `resolver` maps a cwd to a project and `snapshot` turns collector output into frozen view data without doing I/O. `source.LiveSource` wires them together, and `ui.app.DeckApp` polls the source from a worker thread and renders a `Tree`.

**Tech Stack:** Python ≥3.12 (3.13 locally), uv, Textual, psutil, tomli-w, pytest + pytest-cov + pytest-asyncio, ruff, pyright, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-17-m1-read-only-deck-design.md`

## Global Constraints

- Version lives in `pyproject.toml` only: `0.1.0.dev0`.
- `requires-python = ">=3.12"`; runtime deps exactly `textual`, `psutil`, `tomli-w`.
- Every `wezterm` invocation goes through `src/cardak/collectors/wezterm.py` and passes `--no-auto-start`.
- Every read of `~/.claude/` goes through `src/cardak/collectors/claude.py`.
- psutil and `/proc` are touched only by `PsutilTable` in `src/cardak/collectors/processes.py`.
- `src/cardak/collectors/*` import nothing else from `cardak`. `snapshot.py` does no I/O.
- Test fixtures are sanitized: `/home/user/...` paths and made-up project names (`webshop`, `api-gw`). No real usernames, hostnames or other repos.
- CI: `ruff check`, `ruff format --check`, `pyright`, `pytest --cov=cardak --cov-branch --cov-fail-under=80`.
- Commits use conventional-commit subjects ending in `(#2)` and the trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Run everything through `uv run` (e.g. `uv run pytest`), never a bare `pytest`.

## Spec refinements made while planning

The design names these only implicitly. They are made explicit here, and Task 9 writes them back into the spec:
- `src/cardak/source.py` (`LiveSource`) holds the wiring between config, resolver, collectors and snapshot, so `ui` depends only on a small `DeckSource` protocol.
- `PaneProcs.claude_pid` gives the pid of a `claude` process in the pane, so the snapshot can look up its session.
- `PaneRow.cwd` keeps the absolute cwd, which "add as project" needs for panes in Other.
- `Resolved.order` holds the configured project's index, which drives group order.
- Pane titles that look like paths (start with `~`, `/` or `..`) are not shown as `detail`, because the cwd column already shows them.
- `LiveSource` reloads `projects.toml` when its `(mtime_ns, size, inode)` changes, so hand edits are picked up without a restart.
- An unexpected exception in a poll becomes `DeckSnapshot(error="internal error: …")` instead of killing the app.

## Review Focus

1. **`accent` in projects.toml is not a valid color** (`accent = "purpleish"`): the deck must render the name without an accent, not crash. Test in Task 8 (`test_group_label_ignores_invalid_accent`).
2. **A process that is its own parent** (pid 0/1 quirks, or a racy ppid read): summing a tree must terminate. Test in Task 5 (`test_self_parent_does_not_loop`).
3. **A configured path written with `~` or a trailing slash** (`path = "~/repos/webshop/"`): panes in it must still group under that project. Test in Task 3 (`test_config_path_with_tilde_and_trailing_slash`).
4. **projects.toml edited by hand while the deck runs**, twice in quick succession: the next poll uses the new content. Test in Task 7 (`test_hand_edit_is_picked_up`).
5. **A collector raising something unexpected** (e.g. a psutil bug): the deck shows `internal error: …` and keeps polling. Test in Task 8 (`test_poll_exception_shows_internal_error`).

---

### Task 1: Tooling and CI

**Files:**
- Create: `pyproject.toml`, `uv.lock`, `.python-version` (via `uv init`), `src/cardak/__init__.py`, `src/cardak/cli.py`, `tests/test_cli.py`, `.github/workflows/test.yml`

**Interfaces:**
- Produces: `cardak.cli.main(argv: list[str] | None = None) -> int`, `cardak.cli.build_parser() -> argparse.ArgumentParser` (Task 9 extends both).

- [ ] **Step 1: Initialise the package**

Run:
```bash
uv init --package --name cardak --python 3.13 --no-readme --vcs none
uv add textual psutil tomli-w
uv add --dev pytest pytest-cov pytest-asyncio ruff pyright
```
Expected: `pyproject.toml`, `uv.lock`, `.python-version`, `src/cardak/__init__.py` exist.

- [ ] **Step 2: Edit `pyproject.toml`**

Keep the `[build-system]` and `dependencies`/`[dependency-groups]` that uv wrote, and set the rest to:

```toml
[project]
name = "cardak"
version = "0.1.0.dev0"
description = "Project-centric deck for WezTerm panes and Claude Code sessions"
requires-python = ">=3.12"
license = "MIT"
# dependencies = [...]  (as written by uv add)

[project.scripts]
cardak = "cardak.cli:main"
```

Append:

```toml
[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]

[tool.pyright]
include = ["src", "tests"]
pythonVersion = "3.12"
typeCheckingMode = "standard"

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-m 'not live'"
markers = ["live: needs a running WezTerm GUI (run with: uv run pytest -m live)"]
asyncio_mode = "auto"

[tool.coverage.report]
show_missing = true
```

- [ ] **Step 3: Write the failing test** — `tests/test_cli.py`

```python
import pytest

from cardak.cli import main


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.startswith("cardak 0.1.0")
```

- [ ] **Step 4: Run it to see it fail**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cardak.cli'`.

- [ ] **Step 5: Implement** — replace `src/cardak/__init__.py` and create `src/cardak/cli.py`

`src/cardak/__init__.py`:
```python
"""cardak: a project-centric deck for WezTerm panes and Claude Code sessions."""
```

`src/cardak/cli.py`:
```python
"""Command-line entry point."""

import argparse
from importlib.metadata import version


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cardak",
        description="Project-centric deck for WezTerm panes and Claude Code sessions.",
    )
    parser.add_argument("--version", action="version", version=f"cardak {version('cardak')}")
    return parser


def main(argv: list[str] | None = None) -> int:
    build_parser().parse_args(argv)
    return 0
```

- [ ] **Step 6: Add CI** — `.github/workflows/test.yml`

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: astral-sh/setup-uv@v6
        with:
          enable-cache: true

      - run: uv sync --locked

      - name: Lint
        run: uv run ruff check

      - name: Format
        run: uv run ruff format --check

      - name: Typecheck
        run: uv run pyright

      - name: Test with coverage
        run: uv run pytest --cov=cardak --cov-branch --cov-fail-under=80
```

- [ ] **Step 7: Run every CI step locally**

Run: `uv run ruff format && uv run ruff check && uv run pyright && uv run pytest --cov=cardak --cov-branch --cov-fail-under=80`
Expected: all pass; `1 passed`; coverage 100%.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml uv.lock .python-version src tests .github/workflows/test.yml
git commit -m "ci: uv package, ruff, pyright, pytest with an 80% coverage floor (#2)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Config loader and `add_project`

**Files:**
- Create: `src/cardak/config.py`, `tests/test_config.py`

**Interfaces:**
- Produces:
  - `Project(name: str, path: Path, theme: str | None = None, accent: str | None = None, github: str | None = None)` (frozen)
  - `Config(projects: tuple[Project, ...] = (), defaults: dict[str, object] = {})` (frozen)
  - `ConfigError(path: Path, message: str, line: int | None = None)`; `str(err)` is `"<file name> line N: <message>"` or `"<file name>: <message>"`
  - `config_path(cli_value: str | None = None, env: Mapping[str, str] | None = None) -> Path`
  - `load(path: Path) -> Config`
  - `add_project(path: Path, name: str, project_path: Path) -> None`

- [ ] **Step 1: Write the failing tests** — `tests/test_config.py`

```python
from pathlib import Path

import pytest

from cardak.config import Config, ConfigError, Project, add_project, config_path, load

SAMPLE = """\
# my projects
[defaults]
theme = "Tokyo Night"

[[project]]
name  = "webshop"
path  = "~/repos/webshop"
theme = "Catppuccin Mocha"
accent = "#cba6f7"
github = "example/webshop"

[[project]]
name = "api-gw"
path = "/srv/api-gw"
"""


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def test_missing_file_is_empty_config(tmp_path: Path) -> None:
    assert load(tmp_path / "nope.toml") == Config()


def test_load_sample(tmp_path: Path, home: Path) -> None:
    f = tmp_path / "projects.toml"
    f.write_text(SAMPLE)
    cfg = load(f)
    assert cfg.projects == (
        Project(
            name="webshop",
            path=home / "repos/webshop",
            theme="Catppuccin Mocha",
            accent="#cba6f7",
            github="example/webshop",
        ),
        Project(name="api-gw", path=Path("/srv/api-gw")),
    )
    assert cfg.defaults == {"theme": "Tokyo Night"}


def test_invalid_toml_reports_line(tmp_path: Path) -> None:
    f = tmp_path / "projects.toml"
    f.write_text('[[project]]\nname = "x"\npath = \n')
    with pytest.raises(ConfigError) as exc:
        load(f)
    assert exc.value.line == 3
    assert str(exc.value).startswith("projects.toml line 3: ")


def test_project_without_path_is_an_error(tmp_path: Path) -> None:
    f = tmp_path / "projects.toml"
    f.write_text('[[project]]\nname = "x"\n')
    with pytest.raises(ConfigError, match="project #1 needs a string 'name' and 'path'"):
        load(f)


def test_project_must_be_array_of_tables(tmp_path: Path) -> None:
    f = tmp_path / "projects.toml"
    f.write_text('project = "x"\n')
    with pytest.raises(ConfigError, match=r"\[\[project\]\]"):
        load(f)


def test_defaults_must_be_table(tmp_path: Path) -> None:
    f = tmp_path / "projects.toml"
    f.write_text('defaults = "x"\n')
    with pytest.raises(ConfigError, match=r"\[defaults\]"):
        load(f)


def test_add_project_creates_file_and_dirs(tmp_path: Path, home: Path) -> None:
    f = tmp_path / "cfg" / "cardak" / "projects.toml"
    add_project(f, "cardak", home / "repos" / "cardak")
    assert f.read_text() == '[[project]]\nname = "cardak"\npath = "~/repos/cardak"\n'
    assert load(f).projects == (Project(name="cardak", path=home / "repos/cardak"),)


def test_add_project_keeps_comments_and_layout(tmp_path: Path, home: Path) -> None:
    f = tmp_path / "projects.toml"
    f.write_text(SAMPLE)
    add_project(f, "notes", Path("/opt/notes"))
    text = f.read_text()
    assert text.startswith(SAMPLE)
    assert text.endswith('\n[[project]]\nname = "notes"\npath = "/opt/notes"\n')
    assert [p.name for p in load(f).projects] == ["webshop", "api-gw", "notes"]


def test_add_project_refuses_duplicate(tmp_path: Path, home: Path) -> None:
    f = tmp_path / "projects.toml"
    f.write_text(SAMPLE)
    with pytest.raises(ConfigError, match="already configured"):
        add_project(f, "again", home / "repos" / "webshop")


def test_add_project_refuses_broken_file_and_leaves_it(tmp_path: Path) -> None:
    f = tmp_path / "projects.toml"
    f.write_text("this is = = not toml\n")
    with pytest.raises(ConfigError):
        add_project(f, "x", Path("/x"))
    assert f.read_text() == "this is = = not toml\n"
    assert [p.name for p in tmp_path.iterdir()] == ["projects.toml"]


def test_config_path_precedence(tmp_path: Path) -> None:
    assert config_path("/a/b.toml", env={"CARDAK_CONFIG": "/c.toml"}) == Path("/a/b.toml")
    assert config_path(None, env={"CARDAK_CONFIG": "/c.toml"}) == Path("/c.toml")
    assert config_path(None, env={"XDG_CONFIG_HOME": "/xdg"}) == Path("/xdg/cardak/projects.toml")
    assert config_path(None, env={"HOME": str(tmp_path)}) == tmp_path / ".config/cardak/projects.toml"
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cardak.config'`.

- [ ] **Step 3: Implement** — `src/cardak/config.py`

```python
"""Load ~/.config/cardak/projects.toml and append projects to it."""

import os
import re
import tempfile
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import tomli_w


@dataclass(frozen=True)
class Project:
    name: str
    path: Path
    theme: str | None = None
    accent: str | None = None
    github: str | None = None


@dataclass(frozen=True)
class Config:
    projects: tuple[Project, ...] = ()
    defaults: dict[str, object] = field(default_factory=dict)


class ConfigError(Exception):
    def __init__(self, path: Path, message: str, line: int | None = None) -> None:
        self.path = path
        self.message = message
        self.line = line
        where = f"{path.name} line {line}" if line else path.name
        super().__init__(f"{where}: {message}")


def config_path(cli_value: str | None = None, env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    if cli_value:
        return Path(cli_value).expanduser()
    if env.get("CARDAK_CONFIG"):
        return Path(env["CARDAK_CONFIG"]).expanduser()
    if env.get("XDG_CONFIG_HOME"):
        base = Path(env["XDG_CONFIG_HOME"])
    else:
        base = Path(env.get("HOME") or Path.home()) / ".config"
    return base / "cardak" / "projects.toml"


def _opt_str(entry: Mapping[str, object], key: str) -> str | None:
    value = entry.get(key)
    return value if isinstance(value, str) else None


def load(path: Path) -> Config:
    try:
        text = path.read_text()
    except FileNotFoundError:
        return Config()
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        found = re.search(r"at line (\d+)", str(e))
        message = re.sub(r"\s*\(at line \d+, column \d+\)$", "", str(e))
        raise ConfigError(path, message, int(found.group(1)) if found else None) from e

    raw = data.get("project", [])
    if not isinstance(raw, list):
        raise ConfigError(path, "[[project]] must be an array of tables")
    projects = []
    for i, entry in enumerate(raw, 1):
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("name"), str)
            or not isinstance(entry.get("path"), str)
        ):
            raise ConfigError(path, f"project #{i} needs a string 'name' and 'path'")
        projects.append(
            Project(
                name=entry["name"],
                path=Path(entry["path"]).expanduser(),
                theme=_opt_str(entry, "theme"),
                accent=_opt_str(entry, "accent"),
                github=_opt_str(entry, "github"),
            )
        )
    defaults = data.get("defaults", {})
    if not isinstance(defaults, dict):
        raise ConfigError(path, "[defaults] must be a table")
    return Config(projects=tuple(projects), defaults=dict(defaults))


def _with_tilde(path: Path) -> str:
    home = Path.home()
    return f"~/{path.relative_to(home)}" if path.is_relative_to(home) else str(path)


def add_project(path: Path, name: str, project_path: Path) -> None:
    """Append a [[project]] block, keeping the existing text (and its comments) as is."""
    config = load(path)  # raises ConfigError on a broken file, before anything is written
    project_path = project_path.expanduser()
    if any(p.path == project_path for p in config.projects):
        raise ConfigError(path, f"{project_path} is already configured")
    existing = path.read_text() if path.exists() else ""
    block = tomli_w.dumps({"project": [{"name": name, "path": _with_tilde(project_path)}]})
    if not existing or existing.endswith("\n\n"):
        sep = ""
    elif existing.endswith("\n"):
        sep = "\n"
    else:
        sep = "\n\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".projects.", suffix=".toml")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(existing + sep + block)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_config.py -v`
Expected: all PASS. If `test_add_project_creates_file_and_dirs` shows a different TOML layout, check `tomli_w.dumps` output for an array of tables and adjust **only the expected string**, never the append logic.

- [ ] **Step 5: Lint, typecheck, commit**

```bash
uv run ruff format && uv run ruff check && uv run pyright
git add src/cardak/config.py tests/test_config.py
git commit -m "feat(config): load projects.toml and append projects without rewriting it (#2)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Project resolver

**Files:**
- Create: `src/cardak/resolver.py`, `tests/test_resolver.py`

**Interfaces:**
- Consumes: `Config`, `Project` from Task 2.
- Produces:
  - `Kind = Literal["config", "git", "other"]`
  - `Resolved(kind: Kind, key: Path | None, name: str, accent: str | None = None, order: int = 0)` (frozen, hashable)
  - `OTHER: Resolved` (`Resolved("other", None, "Other")`)
  - `GitLookup = Callable[[Path], Path | None]`
  - `run_git(cwd: Path) -> Path | None`
  - `ProjectResolver(config: Config, git: GitLookup = run_git)` with `.resolve(cwd: Path | None) -> Resolved`

- [ ] **Step 1: Write the failing tests** — `tests/test_resolver.py`

```python
import subprocess
from pathlib import Path

import pytest

from cardak.config import Config, Project
from cardak.resolver import OTHER, ProjectResolver, Resolved, run_git


class FakeGit:
    def __init__(self, roots: dict[Path, Path]) -> None:
        self.roots = roots
        self.calls: list[Path] = []

    def __call__(self, cwd: Path) -> Path | None:
        self.calls.append(cwd)
        return self.roots.get(cwd)


CONFIG = Config(
    projects=(
        Project(name="webshop", path=Path("/r/webshop"), accent="#fabd2f"),
        Project(name="shop-docs", path=Path("/r/webshop/docs")),
        Project(name="a", path=Path("/r/a")),
    )
)


def test_longest_configured_prefix_wins() -> None:
    r = ProjectResolver(CONFIG, git=FakeGit({}))
    assert r.resolve(Path("/r/webshop/docs/api")) == Resolved("config", Path("/r/webshop/docs"), "shop-docs", None, 1)
    assert r.resolve(Path("/r/webshop/src")) == Resolved("config", Path("/r/webshop"), "webshop", "#fabd2f", 0)
    assert r.resolve(Path("/r/webshop")).name == "webshop"


def test_prefix_matches_whole_components_only() -> None:
    git = FakeGit({Path("/r/ab"): Path("/r/ab")})
    r = ProjectResolver(CONFIG, git=git)
    assert r.resolve(Path("/r/ab")) == Resolved("git", Path("/r/ab"), "ab")


def test_config_path_with_tilde_and_trailing_slash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from cardak.config import load

    monkeypatch.setenv("HOME", str(tmp_path))
    f = tmp_path / "projects.toml"
    f.write_text('[[project]]\nname = "webshop"\npath = "~/repos/webshop/"\n')
    r = ProjectResolver(load(f), git=FakeGit({}))
    assert r.resolve(tmp_path / "repos/webshop/src").name == "webshop"


def test_git_root_when_not_configured() -> None:
    r = ProjectResolver(CONFIG, git=FakeGit({Path("/x/cardak/src"): Path("/x/cardak")}))
    assert r.resolve(Path("/x/cardak/src")) == Resolved("git", Path("/x/cardak"), "cardak")


def test_other_when_no_repo_or_no_cwd() -> None:
    git = FakeGit({})
    r = ProjectResolver(CONFIG, git=git)
    assert r.resolve(Path("/tmp")) is OTHER
    assert r.resolve(None) is OTHER
    assert git.calls == [Path("/tmp")]


def test_git_lookup_is_cached() -> None:
    git = FakeGit({Path("/x/c"): Path("/x/c")})
    r = ProjectResolver(Config(), git=git)
    r.resolve(Path("/x/c"))
    r.resolve(Path("/x/c"))
    r.resolve(Path("/tmp"))
    r.resolve(Path("/tmp"))
    assert git.calls == [Path("/x/c"), Path("/tmp")]


def test_run_git_finds_toplevel(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "sub").mkdir()
    assert run_git(tmp_path / "sub") == tmp_path.resolve()


def test_run_git_outside_repo_and_missing_dir(tmp_path: Path) -> None:
    assert run_git(tmp_path) is None
    assert run_git(tmp_path / "gone") is None


def test_run_git_missing_binary_or_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", missing)
    assert run_git(Path("/")) is None

    def slow(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired("git", 1)

    monkeypatch.setattr(subprocess, "run", slow)
    assert run_git(Path("/")) is None
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_resolver.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cardak.resolver'`.

- [ ] **Step 3: Implement** — `src/cardak/resolver.py`

```python
"""Map a pane's cwd to a project: configured path first, then git root, else Other."""

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from cardak.config import Config

Kind = Literal["config", "git", "other"]
GitLookup = Callable[[Path], Path | None]


@dataclass(frozen=True)
class Resolved:
    kind: Kind
    key: Path | None
    name: str
    accent: str | None = None
    order: int = 0


OTHER = Resolved("other", None, "Other")


def run_git(cwd: Path) -> Path | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=1,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    top = out.stdout.strip()
    return Path(top) if out.returncode == 0 and top else None


class ProjectResolver:
    def __init__(self, config: Config, git: GitLookup = run_git) -> None:
        indexed = list(enumerate(config.projects))
        # Longest path first so a nested project wins over its parent.
        self._projects = sorted(indexed, key=lambda item: len(item[1].path.parts), reverse=True)
        self._git = git
        self._git_cache: dict[Path, Path | None] = {}

    def resolve(self, cwd: Path | None) -> Resolved:
        if cwd is None:
            return OTHER
        for order, project in self._projects:
            if cwd.is_relative_to(project.path):
                return Resolved("config", project.path, project.name, project.accent, order)
        if cwd not in self._git_cache:
            self._git_cache[cwd] = self._git(cwd)
        root = self._git_cache[cwd]
        return OTHER if root is None else Resolved("git", root, root.name)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_resolver.py -v`
Expected: all PASS.

- [ ] **Step 5: Lint, typecheck, commit**

```bash
uv run ruff format && uv run ruff check && uv run pyright
git add src/cardak/resolver.py tests/test_resolver.py
git commit -m "feat(resolver): group a cwd by configured project, then git root (#2)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: WezTerm collector

**Files:**
- Create: `src/cardak/collectors/__init__.py` (empty docstring), `src/cardak/collectors/wezterm.py`, `tests/fixtures/wezterm_list_basic.json`, `tests/fixtures/wezterm_list_edge.json`, `tests/test_wezterm.py`, `tests/test_live.py`

**Interfaces:**
- Produces:
  - `Pane(pane_id: int, window_id: int, tab_id: int, workspace: str, title: str, cwd: Path | None, tty: str | None, is_active: bool)` (frozen)
  - `WeztermUnavailable(Exception)`; `str(e)` is the human-readable reason
  - `parse_cwd(url: str | None) -> Path | None`
  - `parse_panes(text: str) -> list[Pane]`
  - `find_socket(env: Mapping[str, str] | None = None) -> Path | None`
  - `list_panes(env: Mapping[str, str] | None = None, runner: Runner = subprocess.run) -> list[Pane]`

- [ ] **Step 1: Write the fixtures**

`tests/fixtures/wezterm_list_basic.json` (shape recorded from WezTerm 20240203, sanitized):
```json
[
  {"window_id": 2, "tab_id": 2, "pane_id": 3, "workspace": "default",
   "size": {"rows": 24, "cols": 80, "pixel_width": 800, "pixel_height": 528, "dpi": 96},
   "title": "/tmp", "cwd": "file:///tmp", "cursor_x": 0, "cursor_y": 0,
   "cursor_shape": "Default", "cursor_visibility": "Visible", "left_col": 0, "top_row": 0,
   "tab_title": "", "window_title": "user@devbox:/tmp", "is_active": true, "is_zoomed": false,
   "tty_name": "/dev/pts/9"},
  {"window_id": 0, "tab_id": 0, "pane_id": 0, "workspace": "default",
   "size": {"rows": 24, "cols": 39, "pixel_width": 390, "pixel_height": 528, "dpi": 96},
   "title": "~/repos/cardak", "cwd": "file:///home/user/repos/cardak", "cursor_x": 0, "cursor_y": 0,
   "cursor_shape": "Default", "cursor_visibility": "Visible", "left_col": 0, "top_row": 0,
   "tab_title": "", "window_title": "user@devbox:~/repos/cardak", "is_active": true, "is_zoomed": false,
   "tty_name": "/dev/pts/5"},
  {"window_id": 0, "tab_id": 0, "pane_id": 4, "workspace": "default",
   "size": {"rows": 24, "cols": 40, "pixel_width": 400, "pixel_height": 528, "dpi": 96},
   "title": "..epos/webshop", "cwd": "file:///home/user/repos/webshop", "cursor_x": 0, "cursor_y": 0,
   "cursor_shape": "Default", "cursor_visibility": "Visible", "left_col": 40, "top_row": 0,
   "tab_title": "", "window_title": "user@devbox:~/repos/cardak", "is_active": false, "is_zoomed": false,
   "tty_name": "/dev/pts/10"},
  {"window_id": 1, "tab_id": 1, "pane_id": 2, "workspace": "default",
   "size": {"rows": 24, "cols": 80, "pixel_width": 800, "pixel_height": 528, "dpi": 96},
   "title": "~/repos/api-gw", "cwd": "file:///home/user/repos/api-gw", "cursor_x": 0, "cursor_y": 0,
   "cursor_shape": "Default", "cursor_visibility": "Visible", "left_col": 0, "top_row": 0,
   "tab_title": "", "window_title": "user@devbox:~/repos/api-gw", "is_active": true, "is_zoomed": false,
   "tty_name": "/dev/pts/8"}
]
```

`tests/fixtures/wezterm_list_edge.json`:
```json
[
  {"window_id": 5, "tab_id": 7, "pane_id": 11, "workspace": "default", "title": "zsh",
   "cwd": "", "tty_name": "/dev/pts/3", "is_active": false},
  {"window_id": 5, "tab_id": 7, "pane_id": 12, "workspace": "work", "title": "nvim",
   "cwd": "file://devbox/home/user/repos/my%20proj", "tty_name": "/dev/pts/4", "is_active": true},
  {"window_id": 6, "tab_id": 8, "pane_id": 13, "workspace": "default", "title": "ssh"}
]
```

- [ ] **Step 2: Write the failing tests** — `tests/test_wezterm.py`

```python
import os
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from cardak.collectors.wezterm import (
    Pane,
    WeztermUnavailable,
    find_socket,
    list_panes,
    parse_cwd,
    parse_panes,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_basic() -> None:
    panes = parse_panes((FIXTURES / "wezterm_list_basic.json").read_text())
    assert [p.pane_id for p in panes] == [3, 0, 4, 2]
    assert panes[1] == Pane(
        pane_id=0,
        window_id=0,
        tab_id=0,
        workspace="default",
        title="~/repos/cardak",
        cwd=Path("/home/user/repos/cardak"),
        tty="/dev/pts/5",
        is_active=True,
    )


def test_parse_edge_cases() -> None:
    empty_cwd, hosted, bare = parse_panes((FIXTURES / "wezterm_list_edge.json").read_text())
    assert empty_cwd.cwd is None
    assert hosted.cwd == Path("/home/user/repos/my proj")
    assert hosted.workspace == "work"
    assert bare.cwd is None and bare.tty is None and bare.is_active is False


def test_parse_cwd_rejects_other_schemes() -> None:
    assert parse_cwd("http://example.com/x") is None
    assert parse_cwd(None) is None
    assert parse_cwd("file://") is None


def _dead_pid() -> int:
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


def _runtime(tmp_path: Path) -> Path:
    d = tmp_path / "wezterm"
    d.mkdir()
    return d


def test_explicit_socket_wins(tmp_path: Path) -> None:
    explicit = tmp_path / "explicit"
    explicit.touch()
    (_runtime(tmp_path) / f"gui-sock-{os.getpid()}").touch()
    env = {"WEZTERM_UNIX_SOCKET": str(explicit), "XDG_RUNTIME_DIR": str(tmp_path)}
    assert find_socket(env) == explicit


def test_missing_explicit_socket_falls_through_to_gui(tmp_path: Path) -> None:
    gui = _runtime(tmp_path) / f"gui-sock-{os.getpid()}"
    gui.touch()
    env = {"WEZTERM_UNIX_SOCKET": str(tmp_path / "gone"), "XDG_RUNTIME_DIR": str(tmp_path)}
    assert find_socket(env) == gui


def test_newest_live_gui_socket_wins(tmp_path: Path) -> None:
    d = _runtime(tmp_path)
    old, new = d / f"gui-sock-{os.getppid()}", d / f"gui-sock-{os.getpid()}"
    old.touch()
    new.touch()
    os.utime(old, (1_000, 1_000))
    os.utime(new, (2_000, 2_000))
    assert find_socket({"XDG_RUNTIME_DIR": str(tmp_path)}) == new


def test_dead_gui_socket_skipped_for_mux_socket(tmp_path: Path) -> None:
    d = _runtime(tmp_path)
    (d / f"gui-sock-{_dead_pid()}").touch()
    (d / "gui-sock-notapid").touch()
    (d / "sock").touch()
    assert find_socket({"XDG_RUNTIME_DIR": str(tmp_path)}) == d / "sock"


def test_no_socket(tmp_path: Path) -> None:
    _runtime(tmp_path)
    assert find_socket({"XDG_RUNTIME_DIR": str(tmp_path)}) is None
    assert find_socket({}) is None


class FakeRunner:
    def __init__(
        self, stdout: str = "[]", stderr: str = "", returncode: int = 0, exc: Exception | None = None
    ) -> None:
        self.stdout, self.stderr, self.returncode, self.exc = stdout, stderr, returncode, exc
        self.calls: list[tuple[Sequence[str], dict[str, object]]] = []

    def __call__(self, cmd: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        self.calls.append((cmd, kwargs))
        if self.exc:
            raise self.exc
        return subprocess.CompletedProcess(cmd, self.returncode, self.stdout, self.stderr)


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    sock = tmp_path / "gui-sock"
    sock.touch()
    return {"WEZTERM_UNIX_SOCKET": str(sock), "PATH": "/usr/bin"}


def test_list_panes_calls_cli_without_auto_start(env: dict[str, str]) -> None:
    runner = FakeRunner(stdout=(FIXTURES / "wezterm_list_basic.json").read_text())
    panes = list_panes(env, runner=runner)
    assert len(panes) == 4
    cmd, kwargs = runner.calls[0]
    assert list(cmd) == ["wezterm", "cli", "--no-auto-start", "list", "--format", "json"]
    passed_env = kwargs["env"]
    assert isinstance(passed_env, dict)
    assert passed_env["WEZTERM_UNIX_SOCKET"] == env["WEZTERM_UNIX_SOCKET"]
    assert kwargs["timeout"] == 3


def test_list_panes_without_socket(tmp_path: Path) -> None:
    with pytest.raises(WeztermUnavailable, match="no WezTerm GUI socket found"):
        list_panes({"XDG_RUNTIME_DIR": str(tmp_path)}, runner=FakeRunner())


@pytest.mark.parametrize(
    ("runner", "reason"),
    [
        (FakeRunner(exc=FileNotFoundError("wezterm")), "wezterm is not installed"),
        (FakeRunner(exc=subprocess.TimeoutExpired("wezterm", 3)), "wezterm cli timed out"),
        (FakeRunner(returncode=1, stderr="WARN x\nERROR failed to connect\n"), "ERROR failed to connect"),
        (FakeRunner(returncode=2), "wezterm cli exited with 2"),
        (FakeRunner(stdout="not json"), "unexpected wezterm cli output"),
        (FakeRunner(stdout='[{"title": "no ids"}]'), "unexpected wezterm cli output"),
    ],
)
def test_list_panes_errors(env: dict[str, str], runner: FakeRunner, reason: str) -> None:
    with pytest.raises(WeztermUnavailable, match=reason):
        list_panes(env, runner=runner)
```

`tests/test_live.py`:
```python
import pytest

from cardak.collectors.wezterm import list_panes


@pytest.mark.live
def test_real_wezterm_lists_panes_with_ttys() -> None:
    panes = list_panes()
    assert panes
    assert all(p.tty for p in panes)
```

- [ ] **Step 3: Run them to see them fail**

Run: `uv run pytest tests/test_wezterm.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cardak.collectors'`.

- [ ] **Step 4: Implement**

`src/cardak/collectors/__init__.py`:
```python
"""Collectors: the only code that reads external formats (WezTerm, processes, Claude Code)."""
```

`src/cardak/collectors/wezterm.py`:
```python
"""The only place that talks to WezTerm: socket discovery, `wezterm cli list`, parsing.

Always `--no-auto-start`: without it, a call with no GUI running spawns a headless
mux server. Outside a pane, `wezterm cli` only tries the mux socket, so the GUI's
`gui-sock-<pid>` is found here and passed in WEZTERM_UNIX_SOCKET.
"""

import json
import os
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class Pane:
    pane_id: int
    window_id: int
    tab_id: int
    workspace: str
    title: str
    cwd: Path | None
    tty: str | None
    is_active: bool


class WeztermUnavailable(Exception):
    pass


def parse_cwd(url: str | None) -> Path | None:
    if not url:
        return None
    parts = urlsplit(url)
    if parts.scheme != "file":
        return None
    path = unquote(parts.path)  # the host part (OSC 7 hostname) is ignored
    return Path(path) if path else None


def parse_panes(text: str) -> list[Pane]:
    return [
        Pane(
            pane_id=int(p["pane_id"]),
            window_id=int(p["window_id"]),
            tab_id=int(p["tab_id"]),
            workspace=str(p.get("workspace", "")),
            title=str(p.get("title", "")),
            cwd=parse_cwd(p.get("cwd")),
            tty=p.get("tty_name") or None,
            is_active=bool(p.get("is_active", False)),
        )
        for p in json.loads(text)
    ]


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def find_socket(env: Mapping[str, str] | None = None) -> Path | None:
    env = os.environ if env is None else env
    explicit = env.get("WEZTERM_UNIX_SOCKET")
    if explicit and Path(explicit).exists():
        return Path(explicit)
    runtime = env.get("XDG_RUNTIME_DIR")
    if not runtime:
        return None
    directory = Path(runtime) / "wezterm"
    live = [
        sock
        for sock in directory.glob("gui-sock-*")
        if (pid := sock.name.removeprefix("gui-sock-")).isdigit() and _pid_alive(int(pid))
    ]
    if live:
        return max(live, key=lambda sock: sock.stat().st_mtime)
    mux = directory / "sock"
    return mux if mux.exists() else None


def list_panes(env: Mapping[str, str] | None = None, runner: Runner = subprocess.run) -> list[Pane]:
    env = os.environ if env is None else env
    sock = find_socket(env)
    if sock is None:
        raise WeztermUnavailable("no WezTerm GUI socket found")
    try:
        out = runner(
            ["wezterm", "cli", "--no-auto-start", "list", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=3,
            env={**env, "WEZTERM_UNIX_SOCKET": str(sock)},
        )
    except FileNotFoundError:
        raise WeztermUnavailable("wezterm is not installed") from None
    except subprocess.TimeoutExpired:
        raise WeztermUnavailable("wezterm cli timed out") from None
    if out.returncode != 0:
        lines = out.stderr.strip().splitlines()
        raise WeztermUnavailable(lines[-1] if lines else f"wezterm cli exited with {out.returncode}")
    try:
        return parse_panes(out.stdout)
    except (ValueError, KeyError, TypeError) as e:
        raise WeztermUnavailable(f"unexpected wezterm cli output: {e}") from None
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_wezterm.py -v`
Expected: all PASS. Then run `uv run pytest -m live -v` inside a WezTerm pane: PASS (outside WezTerm with no GUI: fails with `WeztermUnavailable`, which is expected).

- [ ] **Step 6: Lint, typecheck, commit**

```bash
uv run ruff format && uv run ruff check && uv run pyright
git add src/cardak/collectors tests/fixtures tests/test_wezterm.py tests/test_live.py
git commit -m "feat(collectors): list WezTerm panes with socket discovery and no auto-start (#2)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Process collector

**Files:**
- Create: `src/cardak/collectors/processes.py`, `tests/test_processes.py`

**Interfaces:**
- Produces:
  - `ProcInfo(pid: int, ppid: int, tty: str | None, pgid: int, tpgid: int, name: str, cmdline: tuple[str, ...], create_time: float, cpu_pct: float | None, rss: int)` (frozen)
  - `ProcessTable` protocol: `.snapshot() -> list[ProcInfo]`
  - `PsutilTable(proc_root: Path = Path("/proc"))`
  - `PaneProcs(fg_name: str | None, fg_pid: int | None, claude_pid: int | None, cpu_pct: float | None, rss: int, n_procs: int, mcp_cpu_pct: float, mcp_rss: int)` (frozen)
  - `is_claude(p: ProcInfo) -> bool`, `is_mcp_server(p: ProcInfo) -> bool`
  - `summarize(procs: Sequence[ProcInfo], ttys: Iterable[str]) -> dict[str, PaneProcs]`
  - `ProcessSampler(table: ProcessTable | None = None)` with `.sample(ttys: Iterable[str]) -> dict[str, PaneProcs]`

- [ ] **Step 1: Write the failing tests** — `tests/test_processes.py`

```python
import os

from cardak.collectors.processes import (
    PaneProcs,
    ProcInfo,
    ProcessSampler,
    PsutilTable,
    is_claude,
    is_mcp_server,
    summarize,
)

T = "/dev/pts/7"
GIB = 2**30


def proc(
    pid: int,
    ppid: int = 1,
    *,
    tty: str | None = None,
    name: str = "zsh",
    cmdline: tuple[str, ...] | None = None,
    pgid: int | None = None,
    tpgid: int = -1,
    ctime: float = 0.0,
    cpu: float | None = 0.0,
    rss: int = 0,
) -> ProcInfo:
    return ProcInfo(
        pid=pid,
        ppid=ppid,
        tty=tty,
        pgid=pid if pgid is None else pgid,
        tpgid=tpgid,
        name=name,
        cmdline=(name,) if cmdline is None else cmdline,
        create_time=ctime,
        cpu_pct=cpu,
        rss=rss,
    )


def test_idle_shell() -> None:
    procs = [proc(10, tty=T, tpgid=10, cpu=0.5, rss=5_000_000)]
    assert summarize(procs, [T]) == {
        T: PaneProcs(
            fg_name="zsh", fg_pid=10, claude_pid=None, cpu_pct=0.5, rss=5_000_000,
            n_procs=1, mcp_cpu_pct=0.0, mcp_rss=0,
        )
    }


def test_build_under_claude_counts_as_work_and_mcp_is_split() -> None:
    procs = [
        proc(10, tty=T, tpgid=20, cpu=0.0, rss=1),
        proc(20, 10, tty=T, name="claude", tpgid=20, cpu=4.0, rss=300),
        proc(30, 20, name="zsh", cpu=0.0, rss=2),
        proc(31, 30, name="cargo", cmdline=("cargo", "build"), cpu=290.0, rss=5 * GIB),
        proc(40, 20, tty=T, name="npm exec @playw", cmdline=("npm exec @playwright/mcp@latest",), tpgid=20, pgid=20, cpu=3.0, rss=100),
        proc(41, 40, name="node", cmdline=("node", "/x/mcp/server.js"), cpu=1.0, rss=50),
        proc(42, 20, name="uvx", cmdline=("uvx", "some-server"), cpu=0.5, rss=10),
        proc(99, 1, name="other", cpu=50.0, rss=999),
    ]
    out = summarize(procs, [T])[T]
    assert (out.fg_name, out.fg_pid, out.claude_pid) == ("claude", 20, 20)
    assert out.cpu_pct == 294.0
    assert out.rss == 1 + 300 + 2 + 5 * GIB
    assert out.n_procs == 4
    assert (out.mcp_cpu_pct, out.mcp_rss) == (4.5, 160)


def test_foreground_falls_back_to_newest_when_tpgid_unknown() -> None:
    procs = [proc(10, tty=T, ctime=1.0), proc(11, 10, tty=T, name="vim", ctime=2.0)]
    assert summarize(procs, [T])[T].fg_name == "vim"


def test_foreground_prefers_group_leader_in_a_pipeline() -> None:
    procs = [
        proc(10, tty=T, tpgid=20),
        proc(21, 10, tty=T, name="less", pgid=20, tpgid=20, ctime=3.0),
        proc(20, 10, tty=T, name="cat", pgid=20, tpgid=20, ctime=2.0),
    ]
    assert summarize(procs, [T])[T].fg_name == "cat"


def test_cpu_none_only_when_every_process_is_new() -> None:
    assert summarize([proc(10, tty=T, cpu=None)], [T])[T].cpu_pct is None
    both = [proc(10, tty=T, cpu=None), proc(11, 10, cpu=2.0)]
    assert summarize(both, [T])[T].cpu_pct == 2.0


def test_unknown_tty_is_empty() -> None:
    assert summarize([], ["/dev/pts/99"])["/dev/pts/99"] == PaneProcs(
        fg_name=None, fg_pid=None, claude_pid=None, cpu_pct=None, rss=0,
        n_procs=0, mcp_cpu_pct=0.0, mcp_rss=0,
    )


def test_self_parent_does_not_loop() -> None:
    procs = [proc(5, 5, tty=T, tpgid=5), proc(6, 5, cpu=1.0)]
    assert summarize(procs, [T])[T].n_procs == 2


def test_is_claude_variants() -> None:
    assert is_claude(proc(1, name="claude", cmdline=("claude",)))
    assert is_claude(proc(1, name="node", cmdline=("node", "/usr/lib/node_modules/@anthropic-ai/claude-code/cli.js")))
    assert not is_claude(proc(1, name="node", cmdline=("node", "server.js")))
    assert is_claude(proc(1, name="claude", cmdline=()))


def test_is_mcp_server_variants() -> None:
    assert is_mcp_server(proc(1, cmdline=("npm", "exec", "@x/y")))
    assert is_mcp_server(proc(1, cmdline=("npm exec @plaud/mcp",)))
    assert is_mcp_server(proc(1, cmdline=("npx", "-y", "thing")))
    assert is_mcp_server(proc(1, cmdline=("/usr/bin/python", "-m", "my_MCP_server")))
    assert not is_mcp_server(proc(1, cmdline=("bash", "-c", "cargo build")))
    assert not is_mcp_server(proc(1, name="zsh", cmdline=()))


class FakeTable:
    def __init__(self, procs: list[ProcInfo]) -> None:
        self.procs = procs

    def snapshot(self) -> list[ProcInfo]:
        return self.procs


def test_sampler_uses_table() -> None:
    sampler = ProcessSampler(FakeTable([proc(10, tty=T, tpgid=10)]))
    assert sampler.sample([T])[T].fg_pid == 10


def test_psutil_table_reads_this_process() -> None:
    table = PsutilTable()
    first = {p.pid: p for p in table.snapshot()}
    me = first[os.getpid()]
    assert me.ppid == os.getppid()
    assert me.pgid == os.getpgrp()
    assert me.cpu_pct is None
    assert me.rss > 0
    second = {p.pid: p for p in table.snapshot()}
    assert second[os.getpid()].cpu_pct is not None
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_processes.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cardak.collectors.processes'`.

- [ ] **Step 3: Implement** — `src/cardak/collectors/processes.py`

```python
"""Per-tty process trees and their CPU/RAM. PsutilTable is the only psutil and /proc reader."""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import psutil


@dataclass(frozen=True)
class ProcInfo:
    pid: int
    ppid: int
    tty: str | None
    pgid: int
    tpgid: int
    name: str
    cmdline: tuple[str, ...]
    create_time: float
    cpu_pct: float | None  # None on a process's first sample
    rss: int


class ProcessTable(Protocol):
    def snapshot(self) -> list[ProcInfo]: ...


class PsutilTable:
    """Keeps psutil.Process objects between snapshots: cpu_percent needs two samples."""

    def __init__(self, proc_root: Path = Path("/proc")) -> None:
        self._proc_root = proc_root
        self._seen: dict[tuple[int, float], psutil.Process] = {}

    def _groups(self, pid: int) -> tuple[int, int]:
        """(pgrp, tpgid) from /proc/<pid>/stat; the comm field may contain spaces."""
        stat = (self._proc_root / str(pid) / "stat").read_text()
        fields = stat[stat.rindex(")") + 2 :].split()
        return int(fields[2]), int(fields[5])

    def snapshot(self) -> list[ProcInfo]:
        seen: dict[tuple[int, float], psutil.Process] = {}
        infos: list[ProcInfo] = []
        attrs = ["pid", "ppid", "name", "cmdline", "create_time", "terminal", "memory_info"]
        for proc in psutil.process_iter(attrs):
            try:
                info = proc.info
                key = (info["pid"], info["create_time"])
                known = self._seen.get(key)
                tracked = known or proc
                cpu = tracked.cpu_percent(None)
                pgid, tpgid = self._groups(info["pid"])
            except (psutil.Error, OSError, ValueError):
                continue
            seen[key] = tracked
            memory = info["memory_info"]
            infos.append(
                ProcInfo(
                    pid=info["pid"],
                    ppid=info["ppid"] or 0,
                    tty=info["terminal"],
                    pgid=pgid,
                    tpgid=tpgid,
                    name=info["name"] or "?",
                    cmdline=tuple(info["cmdline"] or ()),
                    create_time=info["create_time"],
                    cpu_pct=cpu if known else None,
                    rss=memory.rss if memory else 0,
                )
            )
        self._seen = seen
        return infos


@dataclass(frozen=True)
class PaneProcs:
    fg_name: str | None
    fg_pid: int | None
    claude_pid: int | None
    cpu_pct: float | None
    rss: int
    n_procs: int
    mcp_cpu_pct: float
    mcp_rss: int


MCP_PREFIXES = (("npm", "exec"), ("npx",), ("uvx",))


def _words(p: ProcInfo) -> list[str]:
    # Processes like `npm exec` rewrite argv into one string, so re-split it.
    words = " ".join(p.cmdline).split()
    if words:
        words[0] = Path(words[0]).name
    return words


def is_claude(p: ProcInfo) -> bool:
    words = _words(p)
    if not words:
        return p.name == "claude"
    return words[0] == "claude" or (
        words[0] == "node" and len(words) > 1 and "claude" in words[1]
    )


def is_mcp_server(p: ProcInfo) -> bool:
    words = _words(p)
    if any(tuple(words[: len(prefix)]) == prefix for prefix in MCP_PREFIXES):
        return True
    return "mcp" in " ".join(p.cmdline).lower()


def _subtree(start: Iterable[ProcInfo], children: dict[int, list[ProcInfo]]) -> list[ProcInfo]:
    out: list[ProcInfo] = []
    seen: set[int] = set()
    stack = list(start)
    while stack:
        p = stack.pop()
        if p.pid in seen:
            continue
        seen.add(p.pid)
        out.append(p)
        stack.extend(children.get(p.pid, ()))
    return out


def _foreground(on_tty: Sequence[ProcInfo]) -> ProcInfo | None:
    group = [p for p in on_tty if p.tpgid > 0 and p.pgid == p.tpgid]
    if group:
        leader = next((p for p in group if p.pid == p.pgid), None)
        return leader or max(group, key=lambda p: p.create_time)
    return max(on_tty, key=lambda p: p.create_time, default=None)


def summarize(procs: Sequence[ProcInfo], ttys: Iterable[str]) -> dict[str, PaneProcs]:
    children: dict[int, list[ProcInfo]] = defaultdict(list)
    by_tty: dict[str, list[ProcInfo]] = defaultdict(list)
    for p in procs:
        if p.ppid != p.pid:
            children[p.ppid].append(p)
        if p.tty:
            by_tty[p.tty].append(p)

    result: dict[str, PaneProcs] = {}
    for tty in ttys:
        on_tty = by_tty.get(tty, [])
        tree = _subtree(on_tty, children)
        mcp_roots = [
            c for p in tree if is_claude(p) for c in children.get(p.pid, ()) if is_mcp_server(c)
        ]
        mcp_pids = {p.pid for p in _subtree(mcp_roots, children)}
        work = [p for p in tree if p.pid not in mcp_pids]
        mcp = [p for p in tree if p.pid in mcp_pids]
        fg = _foreground(on_tty)
        claude = next((p for p in work if is_claude(p)), None)
        cpu_values = [p.cpu_pct for p in work]
        cpu = None if all(c is None for c in cpu_values) else sum(c or 0.0 for c in cpu_values)
        result[tty] = PaneProcs(
            fg_name=fg.name if fg else None,
            fg_pid=fg.pid if fg else None,
            claude_pid=claude.pid if claude else None,
            cpu_pct=cpu,
            rss=sum(p.rss for p in work),
            n_procs=len(work),
            mcp_cpu_pct=sum(p.cpu_pct or 0.0 for p in mcp),
            mcp_rss=sum(p.rss for p in mcp),
        )
    return result


class ProcessSampler:
    def __init__(self, table: ProcessTable | None = None) -> None:
        self._table = table or PsutilTable()

    def sample(self, ttys: Iterable[str]) -> dict[str, PaneProcs]:
        return summarize(self._table.snapshot(), ttys)
```

Note: `all(...)` over an empty list is `True`, so a tty with no processes gets `cpu_pct=None`, which is what `test_unknown_tty_is_empty` expects.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_processes.py -v`
Expected: all PASS.

- [ ] **Step 5: Lint, typecheck, commit**

```bash
uv run ruff format && uv run ruff check && uv run pyright
git add src/cardak/collectors/processes.py tests/test_processes.py
git commit -m "feat(collectors): per-pane process trees with CPU/RAM and MCP servers split out (#2)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Claude session lookup

**Files:**
- Create: `src/cardak/collectors/claude.py`, `tests/test_claude.py`

**Interfaces:**
- Produces: `ClaudeSession(session_id: str, name: str | None, cwd: Path | None)` (frozen); `session_meta(pid: int, sessions_dir: Path | None = None) -> ClaudeSession | None`

- [ ] **Step 1: Write the failing tests** — `tests/test_claude.py`

```python
import json
from pathlib import Path

import pytest

from cardak.collectors.claude import ClaudeSession, session_meta


def test_reads_session_file(tmp_path: Path) -> None:
    (tmp_path / "123.json").write_text(
        json.dumps({"pid": 123, "sessionId": "abc", "cwd": "/home/user/repos/webshop",
                    "name": "webshop-12", "status": "busy"})
    )
    assert session_meta(123, tmp_path) == ClaudeSession("abc", "webshop-12", Path("/home/user/repos/webshop"))


def test_missing_file_means_no_prompt_yet(tmp_path: Path) -> None:
    (tmp_path / "123.0f1e.key").write_text("x")
    assert session_meta(123, tmp_path) is None


@pytest.mark.parametrize("content", ["not json", "[]", '{"name": "x"}', '{"sessionId": 5}'])
def test_malformed_file(tmp_path: Path, content: str) -> None:
    (tmp_path / "123.json").write_text(content)
    assert session_meta(123, tmp_path) is None


def test_optional_fields(tmp_path: Path) -> None:
    (tmp_path / "7.json").write_text('{"sessionId": "s"}')
    assert session_meta(7, tmp_path) == ClaudeSession("s", None, None)


def test_default_dir_is_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    d = tmp_path / ".claude" / "sessions"
    d.mkdir(parents=True)
    (d / "9.json").write_text('{"sessionId": "s9"}')
    assert session_meta(9) == ClaudeSession("s9", None, None)
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_claude.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cardak.collectors.claude'`.

- [ ] **Step 3: Implement** — `src/cardak/collectors/claude.py`

```python
"""The only reader of ~/.claude/. M1: session metadata by pid.

~/.claude/sessions/<pid>.json is written after a session's first prompt; before that
only a <pid>.<hash>.key file exists. Not a stable API, so parse defensively.
"""

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ClaudeSession:
    session_id: str
    name: str | None
    cwd: Path | None


def session_meta(pid: int, sessions_dir: Path | None = None) -> ClaudeSession | None:
    directory = sessions_dir or Path.home() / ".claude" / "sessions"
    try:
        data = json.loads((directory / f"{pid}.json").read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("sessionId"), str):
        return None
    name, cwd = data.get("name"), data.get("cwd")
    return ClaudeSession(
        session_id=data["sessionId"],
        name=name if isinstance(name, str) else None,
        cwd=Path(cwd) if isinstance(cwd, str) else None,
    )
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_claude.py -v`
Expected: all PASS.

- [ ] **Step 5: Lint, typecheck, commit**

```bash
uv run ruff format && uv run ruff check && uv run pyright
git add src/cardak/collectors/claude.py tests/test_claude.py
git commit -m "feat(collectors): look up a claude pid's session name (#2)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Snapshot and live source

**Files:**
- Create: `src/cardak/snapshot.py`, `src/cardak/source.py`, `tests/test_snapshot.py`, `tests/test_source.py`

**Interfaces:**
- Consumes: `Pane` (Task 4), `PaneProcs`, `ProcessSampler` (Task 5), `ClaudeSession`, `session_meta` (Task 6), `ProjectResolver`, `Resolved`, `Kind`, `GitLookup`, `run_git` (Task 3), `Config`, `ConfigError`, `load`, `add_project` (Task 2).
- Produces:
  - `PaneRow(pane_id: int, label: str, detail: str | None, cwd: Path | None, cwd_display: str, cpu_pct: float | None, rss: int, is_self: bool)`
  - `Group(kind: Kind, key: Path | None, name: str, accent: str | None, path: str | None, cpu_pct: float, rss: int, panes: tuple[PaneRow, ...])`
  - `DeckSnapshot(groups: tuple[Group, ...] = (), n_panes: int = 0, error: str | None = None, config_error: str | None = None)` with property `n_projects -> int`
  - `ClaudeLookup = Callable[[int], ClaudeSession | None]`
  - `tilde(path: Path, home: Path) -> str`
  - `build_snapshot(panes, procs, resolver, claude_lookup, self_pane_id, home) -> DeckSnapshot`
  - `LiveSource(config_file: Path, env=None, list_panes=wezterm.list_panes, sample=None, claude_lookup=claude.session_meta, git=run_git, home=None)` with `.config_file`, `.can_add -> bool`, `.poll() -> DeckSnapshot`, `.add_project(name: str, path: Path) -> None`

- [ ] **Step 1: Write the failing snapshot tests** — `tests/test_snapshot.py`

```python
from pathlib import Path

from cardak.collectors.claude import ClaudeSession
from cardak.collectors.processes import PaneProcs
from cardak.collectors.wezterm import Pane
from cardak.config import Config, Project
from cardak.resolver import ProjectResolver
from cardak.snapshot import build_snapshot, tilde

HOME = Path("/home/user")
R = HOME / "repos"
CONFIG = Config(
    projects=(
        Project(name="webshop", path=R / "webshop", accent="#fabd2f"),
        Project(name="api-gw", path=R / "api-gw"),
    )
)


def pane(pane_id: int, cwd: Path | None, tty: str | None, title: str = "zsh", window: int = 0) -> Pane:
    return Pane(pane_id=pane_id, window_id=window, tab_id=window, workspace="default", title=title,
                cwd=cwd, tty=tty, is_active=False)


def procs(fg: str = "zsh", cpu: float | None = 1.0, rss: int = 100, claude_pid: int | None = None) -> PaneProcs:
    return PaneProcs(fg_name=fg, fg_pid=1, claude_pid=claude_pid, cpu_pct=cpu, rss=rss, n_procs=1,
                     mcp_cpu_pct=0.0, mcp_rss=0)


def fake_git(cwd: Path) -> Path | None:
    return R / "cardak" if cwd.is_relative_to(R / "cardak") else None


def sessions(pid: int) -> ClaudeSession | None:
    return ClaudeSession("s", "webshop-12", R / "webshop") if pid == 77 else None


PANES = [
    pane(3, Path("/tmp"), "/dev/pts/9", title="/tmp", window=2),
    pane(0, R / "cardak", "/dev/pts/5", title="~/repos/cardak"),
    pane(4, R / "webshop" / "src", "/dev/pts/10", title="..epos/webshop"),
    pane(2, R / "api-gw", "/dev/pts/8", title="vim notes.md", window=1),
    pane(5, R / "webshop", "/dev/pts/11", window=3),
    pane(6, HOME, None, window=4),
]
PROCS = {
    "/dev/pts/9": procs(cpu=None),
    "/dev/pts/5": procs(),
    "/dev/pts/10": procs(fg="claude", cpu=290.0, rss=5_000, claude_pid=77),
    "/dev/pts/8": procs(fg="vim", cpu=2.0),
    "/dev/pts/11": procs(fg="claude", claude_pid=78),
}


def snap():
    return build_snapshot(PANES, PROCS, ProjectResolver(CONFIG, git=fake_git), sessions, 0, HOME)


def test_group_order_and_kinds() -> None:
    s = snap()
    assert [(g.kind, g.name) for g in s.groups] == [
        ("config", "webshop"), ("config", "api-gw"), ("git", "cardak"), ("other", "Other"),
    ]
    assert (s.n_projects, s.n_panes) == (3, 6)
    assert s.groups[0].path == "~/repos/webshop"
    assert s.groups[0].accent == "#fabd2f"
    assert s.groups[3].path is None


def test_rows_labels_and_details() -> None:
    webshop, api_gw, cardak, other = snap().groups
    assert [r.label for r in webshop.panes] == ["claude · webshop-12", "claude (new)"]
    assert webshop.panes[0].detail is None  # path-like title dropped
    assert api_gw.panes[0].label == "vim"
    assert api_gw.panes[0].detail == "vim notes.md"
    assert cardak.panes[0].is_self
    assert [r.label for r in other.panes] == ["zsh", "?"]


def test_cwd_display_relative_to_group() -> None:
    webshop, api_gw, cardak, other = snap().groups
    assert [r.cwd_display for r in webshop.panes] == ["src", "."]
    assert [r.cwd_display for r in other.panes] == ["/tmp", "~"]
    assert other.panes[1].cwd == HOME


def test_totals_treat_unknown_cpu_as_zero() -> None:
    webshop, _, _, other = snap().groups
    assert webshop.cpu_pct == 291.0
    assert webshop.rss == 5_100
    assert other.cpu_pct == 0.0
    assert other.panes[1].cpu_pct is None and other.panes[1].rss == 0


def test_claude_session_without_name() -> None:
    def nameless(pid: int) -> ClaudeSession | None:
        return ClaudeSession("s", None, None)

    s = build_snapshot([PANES[4]], PROCS, ProjectResolver(CONFIG, git=fake_git), nameless, None, HOME)
    assert s.groups[0].panes[0].label == "claude"


def test_identical_inputs_give_equal_snapshots() -> None:
    assert snap() == snap()


def test_tilde() -> None:
    assert tilde(HOME, HOME) == "~"
    assert tilde(R / "x", HOME) == "~/repos/x"
    assert tilde(Path("/srv/x"), HOME) == "/srv/x"
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_snapshot.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cardak.snapshot'`.

- [ ] **Step 3: Implement** — `src/cardak/snapshot.py`

```python
"""Turn collector output into the grouped view the deck renders. Pure: no I/O."""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from cardak.collectors.claude import ClaudeSession
from cardak.collectors.processes import PaneProcs
from cardak.collectors.wezterm import Pane
from cardak.resolver import Kind, ProjectResolver, Resolved

ClaudeLookup = Callable[[int], ClaudeSession | None]


@dataclass(frozen=True)
class PaneRow:
    pane_id: int
    label: str
    detail: str | None
    cwd: Path | None
    cwd_display: str
    cpu_pct: float | None
    rss: int
    is_self: bool


@dataclass(frozen=True)
class Group:
    kind: Kind
    key: Path | None
    name: str
    accent: str | None
    path: str | None
    cpu_pct: float
    rss: int
    panes: tuple[PaneRow, ...]


@dataclass(frozen=True)
class DeckSnapshot:
    groups: tuple[Group, ...] = ()
    n_panes: int = 0
    error: str | None = None
    config_error: str | None = None

    @property
    def n_projects(self) -> int:
        return sum(1 for g in self.groups if g.kind != "other")


def tilde(path: Path, home: Path) -> str:
    if path == home:
        return "~"
    return f"~/{path.relative_to(home)}" if path.is_relative_to(home) else str(path)


def _rank(r: Resolved) -> tuple[int, int, str]:
    if r.kind == "config":
        return (0, r.order, "")
    if r.kind == "git":
        return (1, 0, r.name.lower())
    return (2, 0, "")


def _label(pp: PaneProcs | None, claude_lookup: ClaudeLookup) -> str:
    if pp is None:
        return "?"
    if pp.claude_pid is not None:
        session = claude_lookup(pp.claude_pid)
        if session is None:
            return "claude (new)"
        return f"claude · {session.name}" if session.name else "claude"
    return pp.fg_name or "?"


def _detail(title: str, label: str) -> str | None:
    if not title or title == label or title.startswith(("~", "/", "..")):
        return None  # path-like titles repeat the cwd column
    return title


def _cwd_display(cwd: Path | None, root: Path | None, home: Path) -> str:
    if cwd is None:
        return "?"
    if root is not None and cwd.is_relative_to(root):
        rel = cwd.relative_to(root)
        return "." if rel == Path(".") else str(rel)
    return tilde(cwd, home)


def build_snapshot(
    panes: Iterable[Pane],
    procs: Mapping[str, PaneProcs],
    resolver: ProjectResolver,
    claude_lookup: ClaudeLookup,
    self_pane_id: int | None,
    home: Path,
) -> DeckSnapshot:
    buckets: dict[Resolved, list[Pane]] = {}
    for pane in sorted(panes, key=lambda p: (p.window_id, p.tab_id, p.pane_id)):
        buckets.setdefault(resolver.resolve(pane.cwd), []).append(pane)

    groups: list[Group] = []
    for resolved, members in sorted(buckets.items(), key=lambda item: _rank(item[0])):
        rows = []
        for pane in members:
            pp = procs.get(pane.tty) if pane.tty else None
            label = _label(pp, claude_lookup)
            rows.append(
                PaneRow(
                    pane_id=pane.pane_id,
                    label=label,
                    detail=_detail(pane.title, label),
                    cwd=pane.cwd,
                    cwd_display=_cwd_display(pane.cwd, resolved.key, home),
                    cpu_pct=pp.cpu_pct if pp else None,
                    rss=pp.rss if pp else 0,
                    is_self=pane.pane_id == self_pane_id,
                )
            )
        groups.append(
            Group(
                kind=resolved.kind,
                key=resolved.key,
                name=resolved.name,
                accent=resolved.accent,
                path=tilde(resolved.key, home) if resolved.key else None,
                cpu_pct=sum(r.cpu_pct or 0.0 for r in rows),
                rss=sum(r.rss for r in rows),
                panes=tuple(rows),
            )
        )
    return DeckSnapshot(groups=tuple(groups), n_panes=sum(len(g.panes) for g in groups))
```

- [ ] **Step 4: Run the snapshot tests**

Run: `uv run pytest tests/test_snapshot.py -v`
Expected: all PASS.

- [ ] **Step 5: Write the failing source tests** — `tests/test_source.py`

```python
from collections.abc import Iterable, Mapping
from pathlib import Path

from cardak.collectors.processes import PaneProcs
from cardak.collectors.wezterm import Pane, WeztermUnavailable
from cardak.source import LiveSource

HOME = Path("/home/user")


def panes(env: Mapping[str, str]) -> list[Pane]:
    return [
        Pane(0, 0, 0, "default", "zsh", HOME / "repos/webshop", "/dev/pts/1", True),
        Pane(1, 0, 0, "default", "zsh", HOME / "notes", "/dev/pts/2", False),
    ]


def unavailable(env: Mapping[str, str]) -> list[Pane]:
    raise WeztermUnavailable("no WezTerm GUI socket found")


def sample(ttys: Iterable[str]) -> dict[str, PaneProcs]:
    return {t: PaneProcs("zsh", 1, None, 1.0, 10, 1, 0.0, 0) for t in ttys}


def no_git(cwd: Path) -> Path | None:
    return None


def make(cfg: Path, list_panes=panes, env: dict[str, str] | None = None) -> LiveSource:
    return LiveSource(
        cfg, env={"WEZTERM_PANE": "1"} if env is None else env, list_panes=list_panes, sample=sample,
        claude_lookup=lambda pid: None, git=no_git, home=HOME,
    )


def names(source: LiveSource) -> list[str]:
    return [g.name for g in source.poll().groups]


def test_poll_groups_and_marks_self(tmp_path: Path) -> None:
    cfg = tmp_path / "projects.toml"
    cfg.write_text(f'[[project]]\nname = "webshop"\npath = "{HOME}/repos/webshop"\n')
    snap = make(cfg).poll()
    assert [g.name for g in snap.groups] == ["webshop", "Other"]
    assert snap.groups[1].panes[0].is_self
    assert snap.config_error is None


def test_unreachable_wezterm(tmp_path: Path) -> None:
    snap = make(tmp_path / "p.toml", list_panes=unavailable).poll()
    assert snap.error == "no WezTerm GUI socket found"
    assert snap.groups == ()


def test_broken_config_reports_and_blocks_add(tmp_path: Path) -> None:
    cfg = tmp_path / "projects.toml"
    cfg.write_text("[[project]]\nname = \n")
    source = make(cfg, env={})
    snap = source.poll()
    assert snap.config_error is not None and snap.config_error.startswith("projects.toml line 2")
    assert not source.can_add
    assert names(source) == ["Other"]


def test_add_project_regroups(tmp_path: Path) -> None:
    cfg = tmp_path / "projects.toml"
    source = make(cfg)
    assert names(source) == ["Other"]
    source.add_project("notes", HOME / "notes")
    assert names(source) == ["notes", "Other"]


def test_hand_edit_is_picked_up(tmp_path: Path) -> None:
    cfg = tmp_path / "projects.toml"
    cfg.write_text("")
    source = make(cfg)
    assert names(source) == ["Other"]
    cfg.write_text(f'[[project]]\nname = "notes"\npath = "{HOME}/notes"\n')
    assert names(source) == ["notes", "Other"]
    # A second edit right after the first can land in the same mtime tick; the size
    # differs, so the (mtime, size, inode) stamp still changes.
    cfg.write_text(f'[[project]]\nname = "renamed"\npath = "{HOME}/notes"\n')
    assert names(source) == ["renamed", "Other"]
```

- [ ] **Step 6: Run them to see them fail**

Run: `uv run pytest tests/test_source.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cardak.source'`.

- [ ] **Step 7: Implement** — `src/cardak/source.py`

```python
"""Wire config, resolver and collectors into snapshots for the UI."""

import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from pathlib import Path

from cardak import config as config_mod
from cardak.collectors import claude, wezterm
from cardak.collectors.processes import PaneProcs, ProcessSampler
from cardak.config import Config, ConfigError
from cardak.resolver import GitLookup, ProjectResolver, run_git
from cardak.snapshot import ClaudeLookup, DeckSnapshot, build_snapshot

ListPanes = Callable[[Mapping[str, str]], list[wezterm.Pane]]
Sample = Callable[[Iterable[str]], dict[str, PaneProcs]]
Stamp = tuple[int, int, int] | None


class LiveSource:
    def __init__(
        self,
        config_file: Path,
        env: Mapping[str, str] | None = None,
        list_panes: ListPanes = wezterm.list_panes,
        sample: Sample | None = None,
        claude_lookup: ClaudeLookup = claude.session_meta,
        git: GitLookup = run_git,
        home: Path | None = None,
    ) -> None:
        self.config_file = config_file
        self._env = os.environ if env is None else env
        self._list_panes = list_panes
        self._sample = sample or ProcessSampler().sample
        self._claude_lookup = claude_lookup
        self._git = git
        self._home = home or Path.home()
        pane = self._env.get("WEZTERM_PANE", "")
        self._self_pane = int(pane) if pane.isdigit() else None
        self._config_error: str | None = None
        self._resolver = ProjectResolver(Config(), git)
        self._stamp: Stamp | bool = False  # never equal to a real stamp, so the first check loads
        self._reload_if_changed()

    @property
    def can_add(self) -> bool:
        return self._config_error is None

    def _current_stamp(self) -> Stamp:
        try:
            st = self.config_file.stat()
        except FileNotFoundError:
            return None
        return (st.st_mtime_ns, st.st_size, st.st_ino)

    def _reload_if_changed(self) -> None:
        stamp = self._current_stamp()
        if stamp == self._stamp:
            return
        self._stamp = stamp
        try:
            cfg = config_mod.load(self.config_file)
            self._config_error = None
        except ConfigError as e:
            cfg = Config()
            self._config_error = str(e)
        self._resolver = ProjectResolver(cfg, self._git)

    def poll(self) -> DeckSnapshot:
        self._reload_if_changed()
        try:
            panes = self._list_panes(self._env)
        except wezterm.WeztermUnavailable as e:
            return DeckSnapshot(error=str(e), config_error=self._config_error)
        procs = self._sample([p.tty for p in panes if p.tty])
        snap = build_snapshot(
            panes, procs, self._resolver, self._claude_lookup, self._self_pane, self._home
        )
        return replace(snap, config_error=self._config_error)

    def add_project(self, name: str, path: Path) -> None:
        config_mod.add_project(self.config_file, name, path)
        self._reload_if_changed()
```

- [ ] **Step 8: Run the tests**

Run: `uv run pytest tests/test_snapshot.py tests/test_source.py -v`
Expected: all PASS.

- [ ] **Step 9: Lint, typecheck, commit**

```bash
uv run ruff format && uv run ruff check && uv run pyright
git add src/cardak/snapshot.py src/cardak/source.py tests/test_snapshot.py tests/test_source.py
git commit -m "feat(snapshot): group panes into projects with totals, wired by LiveSource (#2)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: The Textual deck

**Files:**
- Create: `src/cardak/ui/__init__.py`, `src/cardak/ui/widgets.py`, `src/cardak/ui/app.py`, `tests/test_widgets.py`, `tests/test_app.py`

**Interfaces:**
- Consumes: `DeckSnapshot`, `Group`, `PaneRow` (Task 7), `ConfigError` (Task 2).
- Produces:
  - `widgets.fmt_cpu(cpu: float | None) -> str`, `fmt_rss(rss: int) -> str`, `group_label(g: Group) -> Text`, `pane_label(r: PaneRow) -> Text`, `unreachable_label(reason: str) -> Text`
  - `app.DeckSource` protocol (`can_add`, `poll()`, `add_project(name, path)`)
  - `app.DeckApp(source: DeckSource, interval: float = 2.0)` with `.source`, `.snapshot`, `.tree`, `.apply_snapshot(snap)`, `.action_refresh()`
  - `app.AddProjectScreen(path: Path, default_name: str)` (`ModalScreen[str | None]`)

Before writing code, check the installed Textual for `Tree.move_cursor`, `TreeNode.add(..., expand=...)`, `Tree.NodeCollapsed`/`NodeExpanded` and `App.run_test`: `uv run python -c "import textual, inspect; from textual.widgets import Tree; print(textual.__version__, 'move_cursor' in dir(Tree))"`. Adapt only the call sites if a name differs.

- [ ] **Step 1: Write the failing widget tests** — `tests/test_widgets.py`

```python
from pathlib import Path

from cardak.snapshot import Group, PaneRow
from cardak.ui.widgets import fmt_cpu, fmt_rss, group_label, pane_label, unreachable_label


def group(kind: str = "config", accent: str | None = "#fabd2f") -> Group:
    return Group(kind=kind, key=Path("/r/w"), name="webshop", accent=accent,  # type: ignore[arg-type]
                 path="~/r/w", cpu_pct=12.4, rss=1_200 * 2**20, panes=())


def test_formatting() -> None:
    assert fmt_cpu(None) == "–"
    assert fmt_cpu(290.4) == "290%"
    assert fmt_rss(0) == "0M"
    assert fmt_rss(512 * 2**20) == "512M"
    assert fmt_rss(int(1.5 * 2**30)) == "1.5G"


def test_group_labels() -> None:
    assert group_label(group()).plain == "webshop  ~/r/w  CPU 12%  RAM 1.2G"
    assert "not in config" in group_label(group(kind="git")).plain
    assert group_label(group(kind="other", accent=None)).plain.startswith("webshop")


def test_group_label_ignores_invalid_accent() -> None:
    text = group_label(group(accent="purpleish"))
    assert text.plain.startswith("webshop")
    assert all("purpleish" not in str(span.style) for span in text.spans)


def test_pane_label() -> None:
    row = PaneRow(pane_id=1, label="vim", detail="notes.md", cwd=Path("/r/w"), cwd_display=".",
                  cpu_pct=None, rss=0, is_self=True)
    assert pane_label(row).plain == "vim  notes.md  .  CPU –  RAM 0M  (this deck)"


def test_unreachable_label() -> None:
    assert unreachable_label("x").plain == "WezTerm not reachable: x — retrying"
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_widgets.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cardak.ui'`.

- [ ] **Step 3: Implement the widgets**

`src/cardak/ui/__init__.py`:
```python
"""Textual front-end."""
```

`src/cardak/ui/widgets.py`:
```python
"""Row labels for the deck tree."""

from rich.color import Color, ColorParseError
from rich.text import Text

from cardak.snapshot import Group, PaneRow


def fmt_cpu(cpu: float | None) -> str:
    return "–" if cpu is None else f"{cpu:.0f}%"


def fmt_rss(rss: int) -> str:
    gib = rss / 2**30
    return f"{gib:.1f}G" if gib >= 1 else f"{rss / 2**20:.0f}M"


def _valid_color(color: str | None) -> str | None:
    if not color:
        return None
    try:
        Color.parse(color)
    except ColorParseError:
        return None
    return color


def group_label(g: Group) -> Text:
    text = Text()
    if g.kind == "config":
        color = _valid_color(g.accent)
        text.append(g.name, style=f"bold {color}" if color else "bold")
    elif g.kind == "git":
        text.append(g.name, style="bold dim")
        text.append(" · not in config", style="dim italic")
    else:
        text.append(g.name, style="bold")
    if g.path:
        text.append(f"  {g.path}", style="dim")
    text.append(f"  CPU {fmt_cpu(g.cpu_pct)}  RAM {fmt_rss(g.rss)}")
    return text


def pane_label(r: PaneRow) -> Text:
    text = Text()
    text.append(r.label, style="bold")
    if r.detail:
        text.append(f"  {r.detail}")
    text.append(f"  {r.cwd_display}", style="dim")
    text.append(f"  CPU {fmt_cpu(r.cpu_pct)}  RAM {fmt_rss(r.rss)}")
    if r.is_self:
        text.append("  (this deck)", style="italic")
    return text


def unreachable_label(reason: str) -> Text:
    return Text(f"WezTerm not reachable: {reason} — retrying", style="bold red")
```

- [ ] **Step 4: Run the widget tests**

Run: `uv run pytest tests/test_widgets.py -v`
Expected: all PASS.

- [ ] **Step 5: Write the failing app tests** — `tests/test_app.py`

```python
from dataclasses import replace
from pathlib import Path

from textual.pilot import Pilot
from textual.widgets import Input, Static

from cardak.config import ConfigError
from cardak.snapshot import DeckSnapshot, Group, PaneRow
from cardak.ui.app import AddProjectScreen, DeckApp


def row(pane_id: int, cwd: Path | None = None) -> PaneRow:
    return PaneRow(pane_id=pane_id, label="zsh", detail=None, cwd=cwd, cwd_display=".",
                   cpu_pct=1.0, rss=2**20, is_self=False)


def group(kind: str, name: str, key: Path | None, *rows: PaneRow) -> Group:
    return Group(kind=kind, key=key, name=name, accent=None,  # type: ignore[arg-type]
                 path=str(key) if key else None, cpu_pct=1.0, rss=2**20, panes=rows)


SNAP = DeckSnapshot(
    groups=(
        group("config", "webshop", Path("/repos/webshop"), row(1)),
        group("git", "cardak", Path("/repos/cardak"), row(2), row(3)),
        group("other", "Other", None, row(4, cwd=Path("/tmp"))),
    ),
    n_panes=4,
)


class StubSource:
    def __init__(self, *snaps: DeckSnapshot, can_add: bool = True, fail_add: bool = False) -> None:
        self.snaps = list(snaps)
        self.can_add = can_add
        self.fail_add = fail_add
        self.added: list[tuple[str, Path]] = []

    def poll(self) -> DeckSnapshot:
        return self.snaps.pop(0) if len(self.snaps) > 1 else self.snaps[0]

    def add_project(self, name: str, path: Path) -> None:
        if self.fail_add:
            raise ConfigError(Path("projects.toml"), f"{path} is already configured")
        self.added.append((name, path))


class BrokenSource(StubSource):
    def poll(self) -> DeckSnapshot:
        raise RuntimeError("boom")


async def settle(app: DeckApp, pilot: Pilot[None]) -> None:
    await app.workers.wait_for_complete()
    await pilot.pause()


def labels(app: DeckApp) -> list[str]:
    return [str(node.label) for node in app.tree.root.children]


async def test_renders_groups_and_counts() -> None:
    app = DeckApp(StubSource(SNAP), interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        assert [label.split()[0] for label in labels(app)] == ["webshop", "cardak", "Other"]
        assert app.sub_title == "2 projects · 4 panes"
        assert len(app.tree.root.children[1].children) == 2


async def test_collapse_survives_refresh() -> None:
    second = replace(SNAP, n_panes=5)
    app = DeckApp(StubSource(SNAP, second), interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.tree.root.children[1].collapse()
        await pilot.pause()
        app.action_refresh()
        await settle(app, pilot)
        assert app.snapshot == second
        assert [n.is_expanded for n in app.tree.root.children] == [True, False, True]


async def test_cursor_survives_refresh() -> None:
    second = replace(SNAP, n_panes=5)
    app = DeckApp(StubSource(SNAP, second), interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.tree.move_cursor(app.tree.root.children[1].children[1])
        app.action_refresh()
        await settle(app, pilot)
        cursor = app.tree.cursor_node
        assert cursor is not None and cursor.data is not None
        assert cursor.data[1] is not None and cursor.data[1].pane_id == 3


async def test_unreachable_message() -> None:
    app = DeckApp(StubSource(DeckSnapshot(error="no WezTerm GUI socket found")), interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        assert labels(app) == ["WezTerm not reachable: no WezTerm GUI socket found — retrying"]


async def test_poll_exception_shows_internal_error() -> None:
    app = DeckApp(BrokenSource(SNAP), interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        assert "internal error: RuntimeError('boom')" in labels(app)[0]
        assert app.is_running


async def test_config_error_banner() -> None:
    broken = replace(SNAP, config_error="projects.toml line 2: Invalid value")
    app = DeckApp(StubSource(broken, SNAP), interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        assert app.query_one("#banner", Static).display is True
        app.action_refresh()
        await settle(app, pilot)
        assert app.query_one("#banner", Static).display is False


async def test_add_unconfigured_git_group() -> None:
    source = StubSource(SNAP)
    app = DeckApp(source, interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.tree.move_cursor(app.tree.root.children[1])
        await pilot.press("a")
        await pilot.pause()
        assert isinstance(app.screen, AddProjectScreen)
        assert app.screen.query_one(Input).value == "cardak"
        await pilot.press("enter")
        await settle(app, pilot)
        assert source.added == [("cardak", Path("/repos/cardak"))]


async def test_add_pane_in_other_uses_its_cwd_and_escape_cancels() -> None:
    source = StubSource(SNAP)
    app = DeckApp(source, interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.tree.move_cursor(app.tree.root.children[2].children[0])
        await pilot.press("a")
        await pilot.pause()
        assert isinstance(app.screen, AddProjectScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert source.added == []
        await pilot.press("a")
        await pilot.pause()
        await pilot.press("enter")
        await settle(app, pilot)
        assert source.added == [("tmp", Path("/tmp"))]


async def test_add_refused_on_configured_group_and_broken_config() -> None:
    for source in (StubSource(SNAP), StubSource(SNAP, can_add=False)):
        app = DeckApp(source, interval=3600)
        async with app.run_test() as pilot:
            await settle(app, pilot)
            target = 0 if source.can_add else 1
            app.tree.move_cursor(app.tree.root.children[target])
            await pilot.press("a")
            await pilot.pause()
            assert not isinstance(app.screen, AddProjectScreen)
            assert source.added == []


async def test_add_failure_is_reported_not_raised() -> None:
    source = StubSource(SNAP, fail_add=True)
    app = DeckApp(source, interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.tree.move_cursor(app.tree.root.children[1])
        await pilot.press("a")
        await pilot.pause()
        await pilot.press("enter")
        await settle(app, pilot)
        assert app.is_running
        assert source.added == []
```

- [ ] **Step 6: Run them to see them fail**

Run: `uv run pytest tests/test_app.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cardak.ui.app'`.

- [ ] **Step 7: Implement** — `src/cardak/ui/app.py`

```python
"""The deck: a live tree of WezTerm panes grouped by project."""

from pathlib import Path
from typing import Protocol

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Input, Label, Static, Tree
from textual.widgets.tree import TreeNode

from cardak.config import ConfigError
from cardak.snapshot import DeckSnapshot, Group, PaneRow
from cardak.ui.widgets import group_label, pane_label, unreachable_label

NodeData = tuple[Group, PaneRow | None]


class DeckSource(Protocol):
    @property
    def can_add(self) -> bool: ...

    def poll(self) -> DeckSnapshot: ...

    def add_project(self, name: str, path: Path) -> None: ...


def node_id(data: NodeData | None) -> str | None:
    if data is None:
        return None
    group, row = data
    return f"p:{row.pane_id}" if row else f"g:{group.kind}:{group.key}"


class AddProjectScreen(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, path: Path, default_name: str) -> None:
        super().__init__()
        self._path = path
        self._default_name = default_name

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(f"Add {self._path} as a project. Name:")
            yield Input(value=self._default_name, id="name")

    @on(Input.Submitted)
    def _submit(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class DeckApp(App[None]):
    TITLE = "cardak"
    CSS = """
    AddProjectScreen { align: center middle; }
    #dialog { width: 70; height: auto; border: round $accent; padding: 1 2; background: $surface; }
    #banner { display: none; background: $error; color: $text; padding: 0 1; }
    """
    BINDINGS = [
        Binding("a", "add_project", "Add as project"),
        Binding("r", "refresh", "Refresh"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, source: DeckSource, interval: float = 2.0) -> None:
        super().__init__()
        self.source = source
        self.interval = interval
        self.snapshot: DeckSnapshot | None = None
        self._collapsed: set[str] = set()

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("", id="banner")
        yield Tree[NodeData]("projects", id="tree")
        yield Footer()

    @property
    def tree(self) -> Tree[NodeData]:
        return self.query_one("#tree", Tree)

    def on_mount(self) -> None:
        self.tree.show_root = False
        self.action_refresh()
        self.set_interval(self.interval, self.action_refresh)

    def action_refresh(self) -> None:
        self.run_worker(self._poll, thread=True, exclusive=True, group="poll", exit_on_error=False)

    def _poll(self) -> None:
        try:
            snap = self.source.poll()
        except Exception as e:  # a collector bug must not take the deck down
            snap = DeckSnapshot(error=f"internal error: {e!r}")
        self.call_from_thread(self.apply_snapshot, snap)

    def apply_snapshot(self, snap: DeckSnapshot) -> None:
        if snap == self.snapshot:
            return
        self.snapshot = snap
        self.sub_title = f"{snap.n_projects} projects · {snap.n_panes} panes"
        banner = self.query_one("#banner", Static)
        banner.update(f"projects.toml: {snap.config_error}" if snap.config_error else "")
        banner.display = snap.config_error is not None
        self._rebuild(snap)

    def _rebuild(self, snap: DeckSnapshot) -> None:
        tree = self.tree
        cursor = tree.cursor_node
        selected = node_id(cursor.data) if cursor else None
        tree.clear()
        if snap.error:
            tree.root.add_leaf(unreachable_label(snap.error))
            return
        target: TreeNode[NodeData] | None = None
        for group in snap.groups:
            data: NodeData = (group, None)
            node = tree.root.add(
                group_label(group), data=data, expand=node_id(data) not in self._collapsed
            )
            if node_id(data) == selected:
                target = node
            for row in group.panes:
                leaf = node.add_leaf(pane_label(row), data=(group, row))
                if node_id((group, row)) == selected:
                    target = leaf
        if target is not None:
            tree.move_cursor(target)

    @on(Tree.NodeCollapsed)
    def _remember_collapsed(self, event: Tree.NodeCollapsed[NodeData]) -> None:
        if (nid := node_id(event.node.data)) is not None:
            self._collapsed.add(nid)

    @on(Tree.NodeExpanded)
    def _remember_expanded(self, event: Tree.NodeExpanded[NodeData]) -> None:
        if (nid := node_id(event.node.data)) is not None:
            self._collapsed.discard(nid)

    def _add_target(self) -> Path | None:
        node = self.tree.cursor_node
        if node is None or node.data is None:
            return None
        group, row = node.data
        if group.kind == "git":
            return group.key
        if group.kind == "other" and row is not None:
            return row.cwd
        return None

    def action_add_project(self) -> None:
        path = self._add_target()
        if path is None:
            self.notify("Select a repo marked 'not in config', or a pane in Other.", severity="warning")
            return
        if not self.source.can_add:
            self.notify("Fix projects.toml before adding projects.", severity="error")
            return

        def done(name: str | None) -> None:
            if not name:
                return
            try:
                self.source.add_project(name, path)
            except ConfigError as e:
                self.notify(str(e), severity="error")
                return
            self.notify(f"Added {name}")
            self.action_refresh()

        self.push_screen(AddProjectScreen(path, path.name), done)
```

If `Tree.NodeCollapsed[NodeData]` fails at import time ("not subscriptable"), drop the subscript from the two annotations.

- [ ] **Step 8: Run the UI tests**

Run: `uv run pytest tests/test_widgets.py tests/test_app.py -v`
Expected: all PASS. If `test_collapse_survives_refresh` fails because `collapse()` posts no `NodeCollapsed` in this Textual version, record the state in `_rebuild` instead: before `tree.clear()`, set `self._collapsed = {nid for n in tree.root.children if not n.is_expanded and (nid := node_id(n.data))}`, and remove the two handlers.

- [ ] **Step 9: Lint, typecheck, commit**

```bash
uv run ruff format && uv run ruff check && uv run pyright
git add src/cardak/ui tests/test_widgets.py tests/test_app.py
git commit -m "feat(ui): live project tree with add-as-project (#2)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: CLI wiring, docs, live check

**Files:**
- Modify: `src/cardak/cli.py`, `tests/test_cli.py`, `AGENTS.md` (Layout), `PLAN.md` (§11 M1 checkboxes), `CHANGELOG.md` (Unreleased), `docs/superpowers/specs/2026-10-17-m1-read-only-deck-design.md` (refinements)

**Interfaces:**
- Consumes: `config_path` (Task 2), `LiveSource` (Task 7), `DeckApp` (Task 8).

- [ ] **Step 1: Write the failing tests** — append to `tests/test_cli.py`

```python
from pathlib import Path

from cardak.ui.app import DeckApp


def test_tui_runs_deck_with_config_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: list[DeckApp] = []
    monkeypatch.setattr(DeckApp, "run", lambda self: seen.append(self))
    assert main(["--config", str(tmp_path / "p.toml"), "tui"]) == 0
    assert seen[0].source.config_file == tmp_path / "p.toml"  # type: ignore[attr-defined]


def test_default_command_is_tui(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: list[DeckApp] = []
    monkeypatch.setattr(DeckApp, "run", lambda self: seen.append(self))
    monkeypatch.setenv("CARDAK_CONFIG", str(tmp_path / "c.toml"))
    assert main([]) == 0
    assert seen[0].source.config_file == tmp_path / "c.toml"  # type: ignore[attr-defined]
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_cli.py -v`
Expected: the two new tests FAIL (`unrecognized arguments: --config`, and `seen` stays empty).

- [ ] **Step 3: Implement** — replace `src/cardak/cli.py`

```python
"""Command-line entry point: `cardak [--config PATH] [tui]`."""

import argparse
from importlib.metadata import version

from cardak.config import config_path
from cardak.source import LiveSource
from cardak.ui.app import DeckApp


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cardak",
        description="Project-centric deck for WezTerm panes and Claude Code sessions.",
    )
    parser.add_argument("--version", action="version", version=f"cardak {version('cardak')}")
    parser.add_argument(
        "--config",
        help="projects.toml to use (default: $CARDAK_CONFIG, else ~/.config/cardak/projects.toml)",
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("tui", help="open the deck (the default)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command in (None, "tui"):
        DeckApp(LiveSource(config_path(args.config))).run()
    return 0
```

- [ ] **Step 4: Run the full suite with coverage**

Run: `uv run ruff format && uv run ruff check && uv run ruff format --check && uv run pyright && uv run pytest --cov=cardak --cov-branch --cov-fail-under=80`
Expected: all green, total coverage ≥ 80%.

- [ ] **Step 5: Live check inside WezTerm** (manual, by the executor, in a WezTerm pane)

Run: `uv run pytest -m live -v` → PASS.
Run: `uv run cardak`, and check that:
- the panes appear grouped;
- `cd` into another repo in a second pane regroups it within ~2s;
- `yes > /dev/null` in a pane shows ~100% CPU on its row;
- `q` quits.

Use a temporary config (`uv run cardak --config /tmp/cardak-test.toml`) when trying `a`, so the real `~/.config/cardak/projects.toml` is not touched without asking.

- [ ] **Step 6: Update the docs**

`AGENTS.md`, Layout: replace the `src/cardak/` block with:
```
src/cardak/
  config.py  resolver.py  snapshot.py  source.py
  collectors/{wezterm.py, processes.py, claude.py}   # M4 adds hooks.py, transcripts.py
  store.py  alerts.py  daemon.py                      # later milestones
  ui/{app.py, widgets.py}                             # M4 adds detail.py
  wezterm/{generate.py, cardak_snippet.lua}           # M3
  cli.py                  # cardak [tui]; later: web|daemon|hook|hooks install|themes apply
```
and change the tests line to `tests/                   # mirrors src/cardak; fixtures/ holds sanitized recordings`.

`PLAN.md` §11 M1: tick all five boxes.

`CHANGELOG.md`, under `## Unreleased`:
```markdown
### Added
- `cardak` opens a live view of every WezTerm pane grouped by project, with the CPU and memory of everything running in each pane, including builds an agent started. Repos you haven't configured are grouped by their git root, and `a` adds one to your projects. (#2)
```

Spec: append a section `## Refinements made while planning` that copies the bullet list from this plan's "Spec refinements made while planning".

- [ ] **Step 7: Commit, re-pin enola, push, open the PR**

```bash
git add src/cardak/cli.py tests/test_cli.py AGENTS.md PLAN.md CHANGELOG.md docs/superpowers/specs/2026-10-17-m1-read-only-deck-design.md
git commit -m "feat(cli): cardak opens the deck; docs for M1 (#2)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
enola check . || true   # report findings to the user; do not re-pin over a regression
git push -u origin 2-read-only-deck
gh pr create --base main --title "M1: read-only deck (#2)" --body "Closes #2. ..."
```

The PR body has a summary, a test plan (CI checks, the live check from Step 5), and ends with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
