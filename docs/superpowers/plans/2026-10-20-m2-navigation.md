# M2 Navigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enter or a click on a pane in the deck jumps to it and raises its window. `/` fuzzy-filters the tree, and `cardak wezterm install` installs the Lua module that does the raise.

**Architecture:** `collectors.wezterm.activate_pane` selects the pane. `core.navigate.Navigator` then writes an OSC 1337 user var to the deck's own `/dev/tty`, and the shipped Lua handler (`cardak/wezterm/cardak.lua`) turns it into `window:focus()`. `core.filter.filter_snapshot` narrows a snapshot with Textual's fuzzy matcher. `ui.app.DeckApp` wires selection, keys and a search `Input`.

**Tech Stack:** Python ≥3.12, Textual 8 (`textual.fuzzy.Matcher`), WezTerm Lua API (20240203), pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-10-20-m2-navigation-design.md`

## Global Constraints

- Layering: `cli → {ui, wezterm} → core → collectors`. `src/cardak/wezterm/` imports nothing from cardak. `core` never imports `ui` or `cardak.wezterm`. `core/filter.py` is core's only Textual import.
- Every `wezterm` invocation stays in `src/cardak/collectors/wezterm.py` and passes `--no-auto-start`.
- The deck never writes to another pane's tty. The only terminal write is to its own `/dev/tty`.
- cardak never reads or edits `wezterm.lua`. `cardak wezterm install` writes only `cardak.lua`, and overwrites only a file that starts with `-- cardak: generated` (else `--force`).
- User-provided text shown via `notify`, `Label` or `Static.update` is passed through `textual.markup.escape`.
- CI: `ruff check`, `ruff format --check`, `pyright`, `pytest --cov=cardak --cov-branch --cov-fail-under=80`.
- `main` is protected: work on branch `5-navigation`, and merge through a PR with the `test` check green.
- Commit subjects are conventional and end in `(#5)`, with the trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Run everything through `uv run`.
- Imports shown above a test snippet go into the file's top import block (ruff E402/I001), not where the snippet is appended.
- Splitting a long string literal to satisfy E501 is not a deviation and needs no ruling.

## Review Focus

1. **A jump to a pane that closed between the last poll and the keypress:** `activate-pane` fails. The deck must show `Couldn't jump: …` and keep running. Test in Task 3 (`test_jump_failure_is_reported`) and Task 5 (`test_jump_failure_is_notified`).
2. **A search query containing markup or regex-like characters** (`[`, `[/b]`, `.*`): it must neither crash nor mis-render. Test in Task 6 (`test_find_query_with_markup_characters`).
3. **Typing `j`, `k` or `/` into the search box:** the characters go into the query and don't move the cursor. Test in Task 6 (`test_typing_j_k_into_find_does_not_move_cursor`).
4. **The best match sitting in a collapsed group:** the cursor lands on that group instead of jumping to the top. Test in Task 6 (`test_best_match_in_collapsed_group_selects_group`).
5. **`cardak wezterm install` over a hand-written `cardak.lua`:** it refuses, and leaves the file untouched. Test in Task 2 (`test_install_refuses_foreign_file`).

---

### Task 1: `activate_pane` in the WezTerm collector

**Files:**
- Modify: `src/cardak/collectors/wezterm.py` (extract `_run_cli`, add `activate_pane`)
- Test: `tests/test_wezterm.py` (append)

**Interfaces:**
- Produces: `activate_pane(pane_id: int, env: Mapping[str, str] | None = None, runner: Runner = subprocess.run) -> None`, which raises `WeztermUnavailable(reason)`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_wezterm.py`

```python
def test_activate_pane_runs_cli(env: dict[str, str]) -> None:
    runner = FakeRunner(stdout="")
    activate_pane(7, env, runner=runner)
    cmd, kwargs = runner.calls[0]
    assert list(cmd) == ["wezterm", "cli", "--no-auto-start", "activate-pane", "--pane-id", "7"]
    passed_env = kwargs["env"]
    assert isinstance(passed_env, dict)
    assert passed_env["WEZTERM_UNIX_SOCKET"] == env["WEZTERM_UNIX_SOCKET"]


def test_activate_pane_errors(env: dict[str, str], tmp_path: Path) -> None:
    with pytest.raises(WeztermUnavailable, match="pane 7 not found"):
        activate_pane(7, env, runner=FakeRunner(returncode=1, stderr="ERROR pane 7 not found\n"))
    with pytest.raises(WeztermUnavailable, match="no WezTerm GUI socket found"):
        activate_pane(7, {"XDG_RUNTIME_DIR": str(tmp_path)}, runner=FakeRunner())
```

Add `activate_pane` to the existing `from cardak.collectors.wezterm import (...)` list.

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_wezterm.py -q`
Expected: collection error, `ImportError: cannot import name 'activate_pane'`.

- [ ] **Step 3: Implement** — in `src/cardak/collectors/wezterm.py`, replace `list_panes` with:

```python
def _run_cli(args: list[str], env: Mapping[str, str], runner: Runner) -> str:
    """Run `wezterm cli --no-auto-start <args>` against the GUI socket; return stdout."""
    sock = find_socket(env)
    if sock is None:
        raise WeztermUnavailable("no WezTerm GUI socket found")
    try:
        out = runner(
            ["wezterm", "cli", "--no-auto-start", *args],
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
        raise WeztermUnavailable(
            lines[-1] if lines else f"wezterm cli exited with {out.returncode}"
        )
    return out.stdout


def list_panes(env: Mapping[str, str] | None = None, runner: Runner = subprocess.run) -> list[Pane]:
    env = os.environ if env is None else env
    stdout = _run_cli(["list", "--format", "json"], env, runner)
    try:
        return parse_panes(stdout)
    except (ValueError, KeyError, TypeError) as e:
        raise WeztermUnavailable(f"unexpected wezterm cli output: {e}") from None


def activate_pane(
    pane_id: int, env: Mapping[str, str] | None = None, runner: Runner = subprocess.run
) -> None:
    """Select the pane and its tab. Does not raise the window (measured in M0)."""
    env = os.environ if env is None else env
    _run_cli(["activate-pane", "--pane-id", str(pane_id)], env, runner)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_wezterm.py -q`
Expected: all pass (the existing `list_panes` tests prove the extraction kept behaviour).

- [ ] **Step 5: Lint, typecheck, commit**

```bash
uv run ruff format && uv run ruff check && uv run pyright
git add src/cardak/collectors/wezterm.py tests/test_wezterm.py
git commit -m "feat(collectors): activate a WezTerm pane by id (#5)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Lua module and `cardak wezterm install`

**Files:**
- Create: `src/cardak/wezterm/__init__.py`, `src/cardak/wezterm/cardak.lua`, `src/cardak/wezterm/install.py`, `tests/test_install.py`
- Modify: `src/cardak/cli.py`, `tests/test_cli.py`

**Interfaces:**
- Produces:
  - `cardak.wezterm.install.MARKER = "-- cardak: generated"`
  - `module_text() -> str`
  - `default_dir(env: Mapping[str, str] | None = None) -> Path`
  - `InstallResult(path: Path, status: Literal["installed", "updated", "unchanged"])`
  - `InstallError(Exception)`
  - `install(dest_dir: Path, force: bool = False) -> InstallResult`
  - `snippet(dest_dir: Path, env: Mapping[str, str] | None = None) -> str`
  - The CLI subcommand `cardak wezterm install [--dir PATH] [--force]`.

- [ ] **Step 1: Write the failing tests** — `tests/test_install.py`

```python
import os
from pathlib import Path

import pytest

from cardak.wezterm.install import (
    MARKER,
    InstallError,
    default_dir,
    install,
    module_text,
    snippet,
)


def test_packaged_module_is_readable_and_registers_the_handler() -> None:
    text = module_text()
    assert text.startswith(MARKER)
    assert "user-var-changed" in text
    assert "cardak_focus" in text
    assert "function M.apply(config)" in text


def test_fresh_install(tmp_path: Path) -> None:
    result = install(tmp_path / "wezterm")
    assert result.status == "installed"
    assert result.path == tmp_path / "wezterm" / "cardak.lua"
    assert result.path.read_text() == module_text()
    assert result.path.stat().st_mode & 0o777 == 0o644


def test_reinstall_is_unchanged(tmp_path: Path) -> None:
    install(tmp_path)
    assert install(tmp_path).status == "unchanged"


def test_older_cardak_file_is_updated(tmp_path: Path) -> None:
    (tmp_path / "cardak.lua").write_text(MARKER + " (old)\nreturn {}\n")
    assert install(tmp_path).status == "updated"
    assert (tmp_path / "cardak.lua").read_text() == module_text()


def test_install_refuses_foreign_file(tmp_path: Path) -> None:
    mine = "-- my own cardak.lua\nreturn {}\n"
    (tmp_path / "cardak.lua").write_text(mine)
    with pytest.raises(InstallError, match="--force"):
        install(tmp_path)
    assert (tmp_path / "cardak.lua").read_text() == mine
    assert [p.name for p in tmp_path.iterdir()] == ["cardak.lua"]


def test_force_replaces_foreign_file(tmp_path: Path) -> None:
    (tmp_path / "cardak.lua").write_text("-- mine\n")
    assert install(tmp_path, force=True).status == "updated"
    assert (tmp_path / "cardak.lua").read_text() == module_text()


def test_unwritable_dir_is_an_install_error(tmp_path: Path) -> None:
    d = tmp_path / "ro"
    d.mkdir()
    d.chmod(0o500)
    try:
        with pytest.raises(InstallError, match="cannot write"):
            install(d)
    finally:
        d.chmod(0o700)


def test_default_dir(tmp_path: Path) -> None:
    assert default_dir({"XDG_CONFIG_HOME": "/xdg"}) == Path("/xdg/wezterm")
    assert default_dir({"HOME": str(tmp_path)}) == tmp_path / ".config/wezterm"


def test_snippet_for_default_dir(tmp_path: Path) -> None:
    env = {"HOME": str(tmp_path)}
    text = snippet(default_dir(env), env)
    assert "require('cardak').apply(config)" in text
    assert "return config" in text
    assert "package.path" not in text


def test_snippet_for_other_dir_adds_package_path(tmp_path: Path) -> None:
    text = snippet(tmp_path / "elsewhere", {"HOME": str(tmp_path)})
    assert f"package.path = '{tmp_path / 'elsewhere'}/?.lua;' .. package.path" in text


def test_install_does_not_touch_wezterm_lua(tmp_path: Path) -> None:
    config = tmp_path / "wezterm.lua"
    config.write_text("return {}\n")
    before = os.stat(config)
    install(tmp_path)
    assert config.read_text() == "return {}\n"
    assert os.stat(config).st_mtime_ns == before.st_mtime_ns
```

Append to `tests/test_cli.py`:

```python
def test_wezterm_install_prints_snippet(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["wezterm", "install", "--dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"installed {tmp_path / 'cardak.lua'}")
    assert "require('cardak').apply(config)" in out


def test_wezterm_install_refuses_foreign_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "cardak.lua").write_text("-- mine\n")
    assert main(["wezterm", "install", "--dir", str(tmp_path)]) == 1
    assert "--force" in capsys.readouterr().err
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_install.py tests/test_cli.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'cardak.wezterm'`.

- [ ] **Step 3: Implement**

`src/cardak/wezterm/__init__.py`:
```python
"""cardak's WezTerm Lua module and its installer. Imports nothing else from cardak."""
```

`src/cardak/wezterm/cardak.lua`:
```lua
-- cardak: generated by `cardak wezterm install`; reinstalling overwrites this file.
-- Load it from wezterm.lua:  require('cardak').apply(config)
local wezterm = require 'wezterm'

local M = {}

-- The deck sets cardak_focus=<pane_id>:<nonce> on its own pane (the nonce makes every
-- request a change, which is what fires the event). Raise the target pane's window.
local function on_user_var(_, _, name, value)
  if name ~= 'cardak_focus' then return end
  local id = tonumber(value:match('^(%d+)'))
  if not id then return end
  local ok, pane = pcall(wezterm.mux.get_pane, id)
  if not ok or not pane then return end
  local tab = pane:tab()
  if not tab then return end
  tab:activate()
  pane:activate()
  local gui = tab:window():gui_window()
  if gui then gui:focus() end
end

function M.apply(config)
  wezterm.on('user-var-changed', on_user_var)
  return config
end

return M
```

`src/cardak/wezterm/install.py`:
```python
"""Install cardak.lua next to the user's WezTerm config. Never reads or edits wezterm.lua."""

import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Literal

MARKER = "-- cardak: generated"


@dataclass(frozen=True)
class InstallResult:
    path: Path
    status: Literal["installed", "updated", "unchanged"]


class InstallError(Exception):
    pass


def module_text() -> str:
    return files("cardak.wezterm").joinpath("cardak.lua").read_text(encoding="utf-8")


def default_dir(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    if env.get("XDG_CONFIG_HOME"):
        return Path(env["XDG_CONFIG_HOME"]) / "wezterm"
    return Path(env.get("HOME") or Path.home()) / ".config" / "wezterm"


def install(dest_dir: Path, force: bool = False) -> InstallResult:
    text = module_text()
    path = dest_dir / "cardak.lua"
    try:
        existing: str | None = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        existing = None
    except (OSError, UnicodeDecodeError) as e:
        raise InstallError(f"cannot read {path}: {e}") from e
    if existing == text:
        return InstallResult(path, "unchanged")
    if existing is not None and not existing.startswith(MARKER) and not force:
        raise InstallError(
            f"{path} exists and was not written by cardak; use --force to replace it"
        )
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=dest_dir, prefix=".cardak.", suffix=".lua")
    except OSError as e:
        raise InstallError(f"cannot write {path}: {e}") from e
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except OSError as e:
        Path(tmp).unlink(missing_ok=True)
        raise InstallError(f"cannot write {path}: {e}") from e
    return InstallResult(path, "installed" if existing is None else "updated")


def snippet(dest_dir: Path, env: Mapping[str, str] | None = None) -> str:
    load = ""
    if dest_dir != default_dir(env):
        load = f"package.path = '{dest_dir}/?.lua;' .. package.path\n"
    return (
        "Add this line to your wezterm.lua, before `return config`:\n\n"
        f"{load}require('cardak').apply(config)\n\n"
        "No wezterm.lua yet? This is a complete one:\n\n"
        "local wezterm = require 'wezterm'\n"
        "local config = wezterm.config_builder()\n"
        f"{load}require('cardak').apply(config)\n"
        "return config\n\n"
        "WezTerm reloads its config automatically.\n"
    )
```

`src/cardak/cli.py` — replace the whole file:
```python
"""Command-line entry point: `cardak [--config PATH] [tui]`, `cardak wezterm install`."""

import argparse
import sys
from importlib.metadata import version
from pathlib import Path

from cardak.core.config import config_path
from cardak.core.source import LiveSource
from cardak.ui.app import DeckApp
from cardak.wezterm.install import InstallError, default_dir, install, snippet


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
    wez = sub.add_parser("wezterm", help="WezTerm integration")
    wez_sub = wez.add_subparsers(dest="wezterm_command", required=True)
    inst = wez_sub.add_parser("install", help="install cardak.lua so jumps raise windows")
    inst.add_argument("--dir", type=Path, help="target directory (default: ~/.config/wezterm)")
    inst.add_argument("--force", action="store_true", help="replace a cardak.lua not written by cardak")
    return parser


def _wezterm_install(dest: Path | None, force: bool) -> int:
    dest_dir = dest or default_dir()
    try:
        result = install(dest_dir, force=force)
    except InstallError as e:
        print(f"cardak: {e}", file=sys.stderr)
        return 1
    print(f"{result.status} {result.path}\n")
    print(snippet(dest_dir), end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "wezterm":
        return _wezterm_install(args.dir, args.force)
    DeckApp(LiveSource(config_path(args.config))).run()
    return 0
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_install.py tests/test_cli.py -q`
Expected: all pass.

- [ ] **Step 5: Check the wheel ships the Lua file**

Run: `uv build --wheel -q -o /tmp/cardak-wheel && unzip -l /tmp/cardak-wheel/*.whl | grep cardak.lua; rm -rf /tmp/cardak-wheel`
Expected: one line ending in `cardak/wezterm/cardak.lua`. If it is missing, add `[tool.uv.build-backend]` with `source-include` or the backend's data option, re-run, and record a ruling.

- [ ] **Step 6: Lint, typecheck, commit**

```bash
uv run ruff format && uv run ruff check && uv run pyright
git add src/cardak/wezterm src/cardak/cli.py tests/test_install.py tests/test_cli.py
git commit -m "feat(wezterm): Lua focus handler and \`cardak wezterm install\` (#5)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Navigator

**Files:**
- Create: `src/cardak/core/navigate.py`, `tests/test_navigate.py`

**Interfaces:**
- Consumes: `activate_pane`, `WeztermUnavailable` (Task 1).
- Produces:
  - `JumpResult(kind: Literal["raised", "selected", "failed"], message: str | None = None, hint: bool = False)` (frozen)
  - `focus_request(pane_id: int, nonce: str) -> bytes`
  - `Navigator(activate: Callable[[int], None], write_tty: Callable[[bytes], None], in_wezterm: bool, lua_installed: Callable[[], bool])` with `.jump(pane_id: int) -> JumpResult`
  - `Navigator.live(lua_file: Path, env: Mapping[str, str] | None = None) -> Navigator`

- [ ] **Step 1: Write the failing tests** — `tests/test_navigate.py`

```python
import base64
from pathlib import Path

from cardak.collectors.wezterm import WeztermUnavailable
from cardak.core.navigate import JumpResult, Navigator, focus_request


class Recorder:
    def __init__(self, fail_activate: str | None = None, fail_write: str | None = None) -> None:
        self.activated: list[int] = []
        self.written: list[bytes] = []
        self.fail_activate = fail_activate
        self.fail_write = fail_write

    def activate(self, pane_id: int) -> None:
        if self.fail_activate:
            raise WeztermUnavailable(self.fail_activate)
        self.activated.append(pane_id)

    def write(self, data: bytes) -> None:
        if self.fail_write:
            raise OSError(self.fail_write)
        self.written.append(data)


def nav(rec: Recorder, in_wezterm: bool = True, installed: bool = True) -> Navigator:
    return Navigator(rec.activate, rec.write, in_wezterm, lambda: installed)


def test_focus_request_bytes() -> None:
    data = focus_request(12, "99")
    assert data.startswith(b"\x1b]1337;SetUserVar=cardak_focus=")
    assert data.endswith(b"\x07")
    value = data[len(b"\x1b]1337;SetUserVar=cardak_focus=") : -1]
    assert base64.b64decode(value) == b"12:99"


def test_raised() -> None:
    rec = Recorder()
    assert nav(rec).jump(4) == JumpResult("raised")
    assert rec.activated == [4]
    assert len(rec.written) == 1
    assert base64.b64decode(rec.written[0].split(b"=", 2)[2][:-1]).startswith(b"4:")


def test_consecutive_requests_differ() -> None:
    rec = Recorder()
    n = nav(rec)
    n.jump(4)
    n.jump(4)
    assert rec.written[0] != rec.written[1]


def test_jump_failure_is_reported() -> None:
    rec = Recorder(fail_activate="ERROR pane 4 not found")
    assert nav(rec).jump(4) == JumpResult("failed", "Couldn't jump: ERROR pane 4 not found")
    assert rec.written == []


def test_outside_wezterm_only_selects() -> None:
    rec = Recorder()
    result = nav(rec, in_wezterm=False).jump(4)
    assert result.kind == "selected"
    assert result.message is not None and "isn't running in WezTerm" in result.message
    assert rec.written == []


def test_tty_write_failure_only_selects() -> None:
    rec = Recorder(fail_write="no tty")
    result = nav(rec).jump(4)
    assert result.kind == "selected"
    assert result.message == "Selected pane 4; couldn't ask WezTerm to raise it: no tty"


def test_missing_lua_hints_once_and_still_writes() -> None:
    rec = Recorder()
    n = nav(rec, installed=False)
    first = n.jump(4)
    assert first.kind == "selected" and first.hint
    assert first.message == "Selected pane 4. Run `cardak wezterm install` to also raise its window."
    assert n.jump(5) == JumpResult("selected")
    assert len(rec.written) == 2


def test_live_defaults(tmp_path: Path) -> None:
    lua = tmp_path / "cardak.lua"
    n = Navigator.live(lua, env={"WEZTERM_PANE": "3"})
    assert n.in_wezterm
    assert not n.lua_installed()
    lua.write_text("x")
    assert n.lua_installed()
    assert not Navigator.live(lua, env={}).in_wezterm
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_navigate.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'cardak.core.navigate'`.

- [ ] **Step 3: Implement** — `src/cardak/core/navigate.py`

```python
"""Jump to a pane: select it via `wezterm cli`, then ask cardak.lua to raise its window.

The raise request is an OSC 1337 user var written to the deck's *own* terminal; the
Lua handler looks the target up by id. Nothing is written into other panes.
"""

import base64
import itertools
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from cardak.collectors import wezterm

FOCUS_VAR = "cardak_focus"


@dataclass(frozen=True)
class JumpResult:
    kind: Literal["raised", "selected", "failed"]
    message: str | None = None
    hint: bool = False


def focus_request(pane_id: int, nonce: str) -> bytes:
    value = base64.b64encode(f"{pane_id}:{nonce}".encode()).decode()
    return f"\x1b]1337;SetUserVar={FOCUS_VAR}={value}\x07".encode()


def _write_dev_tty(data: bytes) -> None:
    with open("/dev/tty", "wb", buffering=0) as tty:
        tty.write(data)


class Navigator:
    def __init__(
        self,
        activate: Callable[[int], None],
        write_tty: Callable[[bytes], None],
        in_wezterm: bool,
        lua_installed: Callable[[], bool],
    ) -> None:
        self.activate = activate
        self.write_tty = write_tty
        self.in_wezterm = in_wezterm
        self.lua_installed = lua_installed
        self._hinted = False
        self._counter = itertools.count()

    @classmethod
    def live(cls, lua_file: Path, env: Mapping[str, str] | None = None) -> "Navigator":
        env = os.environ if env is None else env
        return cls(
            activate=lambda pane_id: wezterm.activate_pane(pane_id, env),
            write_tty=_write_dev_tty,
            in_wezterm="WEZTERM_PANE" in env,
            lua_installed=lua_file.exists,
        )

    def jump(self, pane_id: int) -> JumpResult:
        try:
            self.activate(pane_id)
        except wezterm.WeztermUnavailable as e:
            return JumpResult("failed", f"Couldn't jump: {e}")
        if not self.in_wezterm:
            return JumpResult(
                "selected",
                f"Selected pane {pane_id}; the deck isn't running in WezTerm, "
                "so its window can't be raised",
            )
        # time keeps requests unique across deck restarts, the counter within one run
        nonce = f"{time.time_ns()}.{next(self._counter)}"
        try:
            self.write_tty(focus_request(pane_id, nonce))
        except OSError as e:
            return JumpResult(
                "selected", f"Selected pane {pane_id}; couldn't ask WezTerm to raise it: {e}"
            )
        if not self.lua_installed():
            if self._hinted:
                return JumpResult("selected")
            self._hinted = True
            return JumpResult(
                "selected",
                f"Selected pane {pane_id}. Run `cardak wezterm install` to also raise its window.",
                hint=True,
            )
        return JumpResult("raised")
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_navigate.py -q`
Expected: all pass.

- [ ] **Step 5: Lint, typecheck, commit**

```bash
uv run ruff format && uv run ruff check && uv run pyright
git add src/cardak/core/navigate.py tests/test_navigate.py
git commit -m "feat(core): navigator that selects a pane and asks WezTerm to raise it (#5)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Fuzzy filter

**Files:**
- Create: `src/cardak/core/filter.py`, `tests/test_filter.py`

**Interfaces:**
- Consumes: `DeckSnapshot`, `Group`, `PaneRow` (M1 `core/snapshot.py`).
- Produces: `FilterResult(snapshot: DeckSnapshot, best_pane_id: int | None, n_matches: int)` (frozen); `filter_snapshot(snap: DeckSnapshot, query: str) -> FilterResult`.

Scores measured with Textual 8.2.8's `Matcher`: `"web"` gives 12.0 for `"webshop zsh src"`, 7.22 for `"api-gw zsh wiki/eb"`, and 0 for `"cardak zsh ."` and `"Other zsh /tmp"`.

- [ ] **Step 1: Write the failing tests** — `tests/test_filter.py`

```python
from pathlib import Path

from cardak.core.filter import filter_snapshot
from cardak.core.resolver import Kind
from cardak.core.snapshot import DeckSnapshot, Group, PaneRow


def row(pane_id: int, label: str = "zsh", cwd: str = ".", detail: str | None = None) -> PaneRow:
    return PaneRow(pane_id=pane_id, label=label, detail=detail, cwd=None, cwd_display=cwd,
                   cpu_pct=1.0, rss=10, is_self=False)


def group(kind: Kind, name: str, *rows: PaneRow) -> Group:
    return Group(kind=kind, key=Path(f"/r/{name}"), name=name, accent=None, path=f"~/r/{name}",
                 cpu_pct=50.0, rss=999, panes=rows)


SNAP = DeckSnapshot(
    groups=(
        group("config", "webshop", row(1, cwd="src"), row(2, label="claude")),
        group("config", "api-gw", row(3, cwd="wiki/eb"), row(4, label="vim", detail="notes.md")),
        group("git", "cardak", row(5)),
        group("other", "Other", row(6, cwd="/tmp")),
    ),
    n_panes=6,
)


def test_blank_query_returns_snapshot_unchanged() -> None:
    for q in ("", "   "):
        result = filter_snapshot(SNAP, q)
        assert result.snapshot is SNAP
        assert (result.best_pane_id, result.n_matches) == (None, 6)


def test_matches_keep_groups_and_order_and_totals() -> None:
    result = filter_snapshot(SNAP, "web")
    names = [(g.name, [r.pane_id for r in g.panes]) for g in result.snapshot.groups]
    assert names == [("webshop", [1, 2]), ("api-gw", [3])]
    assert result.snapshot.groups[0].cpu_pct == 50.0 and result.snapshot.groups[0].rss == 999
    assert result.n_matches == 3
    assert result.snapshot.n_panes == 3


def test_best_match_is_highest_score_first_in_order_on_tie() -> None:
    assert filter_snapshot(SNAP, "web").best_pane_id == 1  # 1 and 2 tie on "webshop…"; 3 scores lower
    assert filter_snapshot(SNAP, "notes").best_pane_id == 4


def test_case_insensitive() -> None:
    assert filter_snapshot(SNAP, "WEB").n_matches == 3


def test_no_match() -> None:
    result = filter_snapshot(SNAP, "zzzz")
    assert result.snapshot.groups == ()
    assert (result.best_pane_id, result.n_matches) == (None, 0)


def test_error_snapshot_passes_through() -> None:
    err = DeckSnapshot(error="no WezTerm GUI socket found")
    assert filter_snapshot(err, "web").snapshot is err
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_filter.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'cardak.core.filter'`.

- [ ] **Step 3: Implement** — `src/cardak/core/filter.py`

```python
"""Fuzzy-filter a snapshot for the deck's search line (Textual's command-palette matcher)."""

from dataclasses import dataclass, replace

from textual.fuzzy import Matcher

from cardak.core.snapshot import DeckSnapshot, Group, PaneRow


@dataclass(frozen=True)
class FilterResult:
    snapshot: DeckSnapshot
    best_pane_id: int | None
    n_matches: int


def _haystack(group: Group, row: PaneRow) -> str:
    return " ".join(part for part in (group.name, row.label, row.detail, row.cwd_display) if part)


def filter_snapshot(snap: DeckSnapshot, query: str) -> FilterResult:
    query = query.strip()
    if not query or snap.error:
        return FilterResult(snap, None, snap.n_panes)
    matcher = Matcher(query)
    groups: list[Group] = []
    best: int | None = None
    best_score = 0.0
    for group in snap.groups:
        kept: list[PaneRow] = []
        for row in group.panes:
            score = matcher.match(_haystack(group, row))
            if score <= 0:
                continue
            kept.append(row)
            if score > best_score:  # strict: ties keep the first in tree order
                best, best_score = row.pane_id, score
        if kept:
            groups.append(replace(group, panes=tuple(kept)))
    n = sum(len(g.panes) for g in groups)
    return FilterResult(replace(snap, groups=tuple(groups), n_panes=n), best, n)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_filter.py -q`
Expected: all pass. If the `"notes"` best-match assertion fails because another haystack scores higher, print the scores with `Matcher("notes").match(...)` and fix **the test data**, not the code; record a ruling.

- [ ] **Step 5: Lint, typecheck, commit**

```bash
uv run ruff format && uv run ruff check && uv run pyright
git add src/cardak/core/filter.py tests/test_filter.py
git commit -m "feat(core): fuzzy-filter a snapshot, keeping groups and order (#5)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Jump from the deck (selection, j/k, CLI wiring)

**Files:**
- Modify: `src/cardak/ui/app.py`, `src/cardak/cli.py`, `tests/test_app.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `JumpResult`, `Navigator.live` (Task 3), `default_dir` (Task 2).
- Produces: the `ui.app.Jumper` protocol (`jump(pane_id: int) -> JumpResult`), `DeckApp(source, navigator: Jumper | None = None, interval: float = 2.0)`, `DeckApp.jump(pane_id: int) -> None`, and `DeckApp.navigator`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_app.py`, adding `from cardak.core.navigate import JumpResult` to its top import block

```python
class StubNavigator:
    def __init__(self, result: JumpResult = JumpResult("raised")) -> None:
        self.result = result
        self.jumps: list[int] = []

    def jump(self, pane_id: int) -> JumpResult:
        self.jumps.append(pane_id)
        return self.result


async def test_enter_on_pane_jumps() -> None:
    nav = StubNavigator()
    app = DeckApp(StubSource(SNAP), navigator=nav, interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.deck_tree.move_cursor(app.deck_tree.root.children[1].children[1])
        await pilot.press("enter")
        await settle(app, pilot)
        assert nav.jumps == [3]


async def test_enter_on_group_toggles_without_jumping() -> None:
    nav = StubNavigator()
    app = DeckApp(StubSource(SNAP), navigator=nav, interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.deck_tree.move_cursor(app.deck_tree.root.children[0])
        await pilot.press("enter")
        await settle(app, pilot)
        assert nav.jumps == []
        assert not app.deck_tree.root.children[0].is_expanded


async def test_jump_failure_is_notified() -> None:
    nav = StubNavigator(JumpResult("failed", "Couldn't jump: [/b] gone"))
    app = DeckApp(StubSource(SNAP), navigator=nav, interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.jump(1)
        await settle(app, pilot)
        await pilot.pause()
        assert app.is_running
        assert [n.message for n in app._notifications] == [r"Couldn't jump: \[/b] gone"]


async def test_j_and_k_move_the_cursor() -> None:
    app = DeckApp(StubSource(SNAP), interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.deck_tree.move_cursor(app.deck_tree.root.children[0])
        await pilot.pause()
        await pilot.press("j", "j")
        assert app.deck_tree.cursor_line == 2
        await pilot.press("k")
        assert app.deck_tree.cursor_line == 1
```

Append to `tests/test_cli.py`, adding `from cardak.core.navigate import Navigator` to its top import block:

```python
def test_tui_gets_a_live_navigator(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: list[DeckApp] = []
    monkeypatch.setattr(DeckApp, "run", lambda self: seen.append(self))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert main(["--config", str(tmp_path / "p.toml")]) == 0
    nav = seen[0].navigator
    assert isinstance(nav, Navigator)
    (tmp_path / "wezterm").mkdir()
    (tmp_path / "wezterm" / "cardak.lua").write_text("x")
    assert nav.lua_installed()
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_app.py tests/test_cli.py -q`
Expected: failures with `TypeError: DeckApp.__init__() got an unexpected keyword argument 'navigator'` and `AttributeError: 'DeckApp' object has no attribute 'jump'`.

- [ ] **Step 3: Implement** — in `src/cardak/ui/app.py`:

Add the imports `from cardak.core.navigate import JumpResult` and, after `DeckSource`, the protocol:
```python
class Jumper(Protocol):
    def jump(self, pane_id: int) -> JumpResult: ...
```

Change `BINDINGS` and `__init__`:
```python
    BINDINGS = [
        Binding("a", "add_project", "Add as project"),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("r", "refresh", "Refresh"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(
        self, source: DeckSource, navigator: Jumper | None = None, interval: float = 2.0
    ) -> None:
        super().__init__()
        self.source = source
        self.navigator = navigator
        self.interval = interval
        self.snapshot: DeckSnapshot | None = None
        self._collapsed: set[str] = set()
        self._poll_worker: Worker[None] | None = None
```

Add these methods to `DeckApp`:
```python
    def action_cursor_down(self) -> None:
        self.deck_tree.action_cursor_down()

    def action_cursor_up(self) -> None:
        self.deck_tree.action_cursor_up()

    @on(Tree.NodeSelected)
    def _selected(self, event: Tree.NodeSelected[NodeData]) -> None:
        data = event.node.data
        if data is not None and data[1] is not None:  # a group row toggles itself (auto_expand)
            self.jump(data[1].pane_id)

    def jump(self, pane_id: int) -> None:
        navigator = self.navigator
        if navigator is None:
            return

        def work() -> None:
            try:
                result = navigator.jump(pane_id)
            except Exception as e:  # a navigator bug must not take the deck down
                result = JumpResult("failed", f"Couldn't jump: {e!r}")
            self.call_from_thread(self._report_jump, result)

        self.run_worker(work, thread=True, group="jump", exit_on_error=False)

    def _report_jump(self, result: JumpResult) -> None:
        if result.message:
            severity = "error" if result.kind == "failed" else "information"
            self.notify(escape(result.message), severity=severity)
```

In `src/cardak/cli.py`, add the imports `from cardak.core.navigate import Navigator`, and replace the last two lines of `main` with:
```python
    lua_file = default_dir() / "cardak.lua"
    DeckApp(LiveSource(config_path(args.config)), navigator=Navigator.live(lua_file)).run()
    return 0
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_app.py tests/test_cli.py -q`
Expected: all pass. `app._notifications` is Textual's private notification store. If that name differs in the installed Textual, capture `notify` instead with `monkeypatch.setattr(app, "notify", ...)`, and record a ruling.

- [ ] **Step 5: Lint, typecheck, commit**

```bash
uv run ruff format && uv run ruff check && uv run pyright
git add src/cardak/ui/app.py src/cardak/cli.py tests/test_app.py tests/test_cli.py
git commit -m "feat(ui): Enter or click on a pane jumps to it; j/k navigation (#5)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Inline fuzzy find

**Files:**
- Modify: `src/cardak/ui/app.py`, `src/cardak/ui/widgets.py`, `tests/test_app.py`, `tests/test_widgets.py`

**Interfaces:**
- Consumes: `filter_snapshot`, `FilterResult` (Task 4); `DeckApp.jump` (Task 5).
- Produces:
  - `widgets.no_match_label(query: str) -> Text`;
  - `DeckApp.raw: DeckSnapshot | None` (the last polled snapshot);
  - `DeckApp.snapshot` (now the *displayed*, filtered snapshot);
  - `DeckApp.find_query: str`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_widgets.py`, adding `no_match_label` to its existing `cardak.ui.widgets` import:
```python
def test_no_match_label() -> None:
    assert no_match_label("[/b]").plain == 'No pane matches "[/b]"'
```

Append to `tests/test_app.py` (`Input` and `Pilot` are already imported). In these tests SNAP's groups are `webshop`, `cardak` and `Other`, and only the webshop pane matches `web`:
```python
async def open_find(app: DeckApp, pilot: Pilot[None], *keys: str) -> None:
    await pilot.press("slash")
    await pilot.pause()
    await pilot.press(*keys)
    await pilot.pause()


async def test_find_filters_and_enter_jumps_to_best() -> None:
    nav = StubNavigator()
    app = DeckApp(StubSource(SNAP), navigator=nav, interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await open_find(app, pilot, "w", "e", "b")
        assert app.find_query == "web"
        assert [label.split()[0] for label in labels(app)] == ["webshop"]
        assert app.sub_title == '1 of 4 panes match "web"'
        await pilot.press("enter")
        await settle(app, pilot)
        assert nav.jumps == [1]


async def test_escape_restores_the_full_tree() -> None:
    app = DeckApp(StubSource(SNAP), interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await open_find(app, pilot, "w", "e", "b")
        await pilot.press("escape")
        await pilot.pause()
        assert app.find_query == ""
        assert len(labels(app)) == 3
        assert app.query_one("#find", Input).display is False
        assert app.focused is app.deck_tree
        assert app.sub_title == "2 projects · 4 panes"


async def test_filter_survives_a_refresh() -> None:
    second = replace(SNAP, n_panes=5)
    app = DeckApp(StubSource(SNAP, second), interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await open_find(app, pilot, "w", "e", "b")
        app.action_refresh()
        await settle(app, pilot)
        assert app.raw == second
        assert [label.split()[0] for label in labels(app)] == ["webshop"]


async def test_no_match_shows_a_message_and_enter_does_nothing() -> None:
    nav = StubNavigator()
    app = DeckApp(StubSource(SNAP), navigator=nav, interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await open_find(app, pilot, "z", "z", "z")
        assert labels(app) == ['No pane matches "zzz"']
        await pilot.press("enter")
        await settle(app, pilot)
        assert nav.jumps == []


async def test_typing_j_k_into_find_does_not_move_cursor() -> None:
    app = DeckApp(StubSource(SNAP), interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        line = app.deck_tree.cursor_line
        await open_find(app, pilot, "j", "k", "slash")
        assert app.query_one("#find", Input).value == "jk/"
        assert app.deck_tree.cursor_line == line


async def test_find_query_with_markup_characters() -> None:
    app = DeckApp(StubSource(SNAP), interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await open_find(app, pilot, "left_square_bracket", "slash", "b", "right_square_bracket")
        await pilot.pause()
        assert app.is_running
        assert labels(app) == ['No pane matches "[/b]"']


async def test_best_match_in_collapsed_group_selects_group() -> None:
    app = DeckApp(StubSource(SNAP), interval=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.deck_tree.root.children[0].collapse()
        await pilot.pause()
        await open_find(app, pilot, "w", "e", "b")
        cursor = app.deck_tree.cursor_node
        assert cursor is not None and cursor.data is not None
        assert cursor.data[1] is None and cursor.data[0].name == "webshop"
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_app.py tests/test_widgets.py -q`
Expected: `tests/test_widgets.py` errors at collection with `ImportError: cannot import name 'no_match_label'`; the new app tests fail with `AttributeError: 'DeckApp' object has no attribute 'find_query'` (or on the `slash` key doing nothing).

- [ ] **Step 3: Implement**

`src/cardak/ui/widgets.py`, append:
```python
def no_match_label(query: str) -> Text:
    return Text(f'No pane matches "{query}"', style="dim italic")
```

`src/cardak/ui/app.py`:
- Imports: add `from cardak.core.filter import filter_snapshot`, and add `no_match_label` to the widgets import.
- CSS: add `#find { display: none; }`.
- `BINDINGS`: add `Binding("slash", "find", "Find")` after `"a"`, and `Binding("escape", "clear_find", "Clear find", show=False)`.
- `__init__`: add `self.raw: DeckSnapshot | None = None` and `self.find_query = ""`.
- `compose`: yield `Input(placeholder="find: project, process, title or path", id="find")` between the banner and the tree.
- Replace `apply_snapshot` and `_rebuild`, and add the find handlers:

```python
    def apply_snapshot(self, snap: DeckSnapshot) -> None:
        if snap == self.raw:
            return
        self.raw = snap
        banner = self.query_one("#banner", Static)
        banner.update(escape(f"projects.toml: {snap.config_error}") if snap.config_error else "")
        banner.display = snap.config_error is not None
        self._render(follow_best=False)

    def _render(self, follow_best: bool) -> None:
        raw = self.raw
        if raw is None:
            return
        result = filter_snapshot(raw, self.find_query)
        self.snapshot = result.snapshot
        self._best = result.best_pane_id
        query = self.find_query.strip()
        if query:
            self.sub_title = f'{result.n_matches} of {raw.n_panes} panes match "{query}"'
        else:
            self.sub_title = f"{raw.n_projects} projects · {raw.n_panes} panes"
        self._rebuild(
            result.snapshot,
            focus_pane=result.best_pane_id if follow_best else None,
            empty_query=query or None,
        )

    def _rebuild(
        self, snap: DeckSnapshot, focus_pane: int | None = None, empty_query: str | None = None
    ) -> None:
        tree = self.deck_tree
        cursor = tree.cursor_node
        selected = node_id(cursor.data) if cursor else None
        tree.clear()
        if snap.error:
            tree.root.add_leaf(unreachable_label(snap.error))
            return
        if not snap.groups and empty_query:
            tree.root.add_leaf(no_match_label(empty_query))
            return
        target: TreeNode[NodeData] | None = None
        for group in snap.groups:
            data: NodeData = (group, None)
            node = tree.root.add(
                group_label(group), data=data, expand=node_id(data) not in self._collapsed
            )
            if focus_pane is None and node_id(data) == selected:
                target = node
            for row in group.panes:
                leaf = node.add_leaf(pane_label(row), data=(group, row))
                if focus_pane is not None:
                    if row.pane_id == focus_pane:
                        # a leaf in a collapsed group has no line; land on its group instead
                        target = leaf if node.is_expanded else node
                elif node_id((group, row)) == selected:
                    target = leaf
        if target is not None:
            # New nodes get their line numbers on the next refresh; moving now lands on line 0.
            self.call_after_refresh(tree.move_cursor, target)

    def action_find(self) -> None:
        find = self.query_one("#find", Input)
        find.display = True
        find.focus()

    def action_clear_find(self) -> None:
        find = self.query_one("#find", Input)
        if not find.display:
            return
        find.value = ""
        find.display = False
        self.find_query = ""
        self._render(follow_best=False)
        self.deck_tree.focus()

    @on(Input.Changed, "#find")
    def _find_changed(self, event: Input.Changed) -> None:
        if event.value == self.find_query:
            return
        self.find_query = event.value
        self._render(follow_best=True)

    @on(Input.Submitted, "#find")
    def _find_submitted(self, event: Input.Submitted) -> None:
        if self._best is not None:
            self.jump(self._best)
```

Also add `self._best: int | None = None` in `__init__`. The existing M1 test `test_collapse_survives_refresh` asserts `app.snapshot == second`. That still holds, because with an empty query the displayed snapshot is the raw one.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_app.py tests/test_widgets.py -q`
Expected: all pass. If `pilot.press("left_square_bracket")` is not the key name in this Textual, use `"["`, and record a ruling.

- [ ] **Step 5: Full suite, lint, typecheck, commit**

```bash
uv run ruff format && uv run ruff check && uv run pyright && uv run pytest --cov=cardak --cov-branch --cov-fail-under=80 -q
git add src/cardak/ui tests/test_app.py tests/test_widgets.py
git commit -m "feat(ui): '/' fuzzy-filters the tree; Enter jumps to the best match (#5)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Live check, docs, PR

**Files:**
- Modify: `AGENTS.md`, `PLAN.md` (§11 M2 boxes), `CHANGELOG.md` (Unreleased)

- [ ] **Step 1: Live check** (manual, by the executor; never touch the user's real `~/.config/wezterm`)

```bash
S=<scratchpad>; mkdir -p $S/wez
uv run cardak wezterm install --dir $S/wez          # prints "installed …/cardak.lua" + snippet with package.path
printf "local wezterm = require 'wezterm'\nlocal config = wezterm.config_builder()\npackage.path = '$S/wez/?.lua;' .. package.path\nrequire('cardak').apply(config)\nreturn config\n" > $S/wez/wezterm.lua
XDG_CONFIG_HOME=$S/xdg wezterm --config-file $S/wez/wezterm.lua start --cwd ~/repos/cardak -- uv run --project ~/repos/cardak cardak --config $S/cardak-test.toml &
```
Then use `wezterm cli --no-auto-start spawn --new-window --cwd /tmp` to open a second window, and send keys to the deck pane with `wezterm cli send-text --no-paste`: arrows (one per write) onto the `/tmp` pane row, then `\r`. Verify with `xprop -root _NET_ACTIVE_WINDOW` and the window's `_NET_WM_NAME` that the `/tmp` window became active. Then send `/tmp` + `\r` to the deck after re-raising it, and check the same.

A KWin raise is only allowed while WezTerm already has focus. Make the deck window active once via the EWMH fallback in `spikes/02_activate_pane.py --fallback`, and only when the user is not typing in another window. Close every test window afterwards.

Expected: both jumps make the target window active. Also confirm the hint path: with `XDG_CONFIG_HOME` pointing at an empty directory, a jump shows `Run \`cardak wezterm install\` …` once.

- [ ] **Step 2: Docs**

`AGENTS.md`:
- In **Code**, extend the layering line to: "`cli → {ui, wezterm} → core → collectors`; `wezterm/` (the Lua module and its installer) imports nothing from cardak".
- In **WezTerm (measured in M0)**, replace the `activate-pane` bullet with: "**Jumping** (`core/navigate.py`): `activate-pane` selects the pane, then the deck writes `OSC 1337 SetUserVar=cardak_focus=base64(<pane_id>:<nonce>)` to its **own** `/dev/tty`, and `cardak.lua` raises the target window. It never writes to another pane's tty. KWin allows the raise because the request comes from the focused WezTerm window. `cardak wezterm install` installs the module; users add `require('cardak').apply(config)` themselves."
- In **Commits and PRs**, add: "`main` is protected: the `test` check must pass on an up-to-date branch, changes land only through PRs, and the rule binds admins."

`PLAN.md` §11 M2: tick both boxes.

`CHANGELOG.md` under `## Unreleased`:
```markdown
### Added
- Press Enter on a pane (or click it) to jump there: cardak selects its tab and brings its window to the front. Run `cardak wezterm install` once and add the line it prints to your `wezterm.lua` to enable the window raise. (#5)
- Press `/` to fuzzy-find a pane by project, program, title or folder; Enter jumps to the best match and Esc shows everything again. `j`/`k` move up and down. (#5)
```

- [ ] **Step 3: Commit, enola, push, PR**

```bash
git add AGENTS.md PLAN.md CHANGELOG.md
git commit -m "docs: M2 navigation (#5)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
enola check . 2>/dev/null | sed -n '/^New findings/,/^No --fail-on/p'   # report new findings to the user; cycles must be zero
```

Push and open a PR only after the final review (executing-plans → finishing-a-development-branch).
