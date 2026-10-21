"""Run the real cardak.lua (Lua 5.4, as embedded in WezTerm) against a fake `wezterm` API."""

import json
from importlib.resources import files
from pathlib import Path
from typing import Any

import pytest
from lupa.lua54 import LuaRuntime

from cardak.core.config import Config, Project
from cardak.wezterm.generate import render_projects_lua

MODULE = files("cardak.wezterm").joinpath("cardak.lua").read_text()

HARNESS = """
handlers, windows = {}, {}
fake = {
  on = function(name, fn)
    handlers[name] = handlers[name] or {}
    table.insert(handlers[name], fn)
  end,
  mux = { get_pane = function(id) error('pane id ' .. id .. ' not found') end },
  gui = { gui_windows = function() return windows end },
  json_parse = function(s) return py_json(s) end,
  time = { now = function() return { format = function() return NOW_MS end } end },
}
NOW_MS = '1000'
function make_window(id, cwd, scheme)
  local w = { id = id, cwd = cwd, sets = 0, focused = false, pane = id * 10 }
  if scheme then w.overrides = { color_scheme = scheme } end
  function w:window_id() return self.id end
  function w:is_focused() return self.focused end
  function w:active_pane()
    local win = self
    return {
      get_current_working_dir = function() return 'file://' .. win.cwd end,
      pane_id = function() return win.pane end,
    }
  end
  function w:get_config_overrides() return self.overrides end
  function w:set_config_overrides(o) self.overrides = o; self.sets = self.sets + 1 end
  table.insert(windows, w)
  return w
end
function fire(name, ...)
  for _, fn in ipairs(handlers[name] or {}) do fn(...) end
end
function scheme_of(w) return w.overrides and w.overrides.color_scheme end
package.preload['wezterm'] = function() return fake end
package.preload['cardak_projects'] = function()
  if DATA == nil then error('module not found') end
  return assert(load(DATA))()
end
"""


class Wez:
    def __init__(self, data: str | None) -> None:
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        g: Any = self.lua.globals()  # Lua tables are dynamic; attribute access is untyped
        self.g = g
        g.py_json = lambda s: self.lua.table_from(json.loads(s))
        g.DATA = data
        self.lua.execute(HARNESS)
        g.M = self.lua.execute(MODULE)
        self.lua.eval("M.apply({})")

    def window(self, id: int, cwd: str, scheme: str | None = None) -> Any:
        return self.g.make_window(id, cwd, scheme)

    def status(self, w: Any) -> None:
        self.g.fire("update-status", w, None)

    def preview(self, w: Any, path: str, scheme: str) -> None:
        value = json.dumps({"path": path, "scheme": scheme, "n": 1})
        self.g.fire("user-var-changed", w, None, "cardak_preview", value)

    def scheme(self, w: Any) -> str | None:
        return self.g.scheme_of(w)


def data(*projects: Project, default: str | None = None) -> str:
    defaults: dict[str, object] = {"theme": default} if default else {}
    return render_projects_lua(Config(projects=projects, defaults=defaults), None)[0]


WEB = Project(name="web", path=Path("/r/web"), theme="Nord")


def test_window_takes_its_project_scheme_else_the_default() -> None:
    wez = Wez(data(WEB, default="Dflt"))
    inside, outside = wez.window(1, "/r/web/src"), wez.window(2, "/elsewhere")
    wez.status(inside)
    wez.status(outside)
    assert (wez.scheme(inside), wez.scheme(outside)) == ("Nord", "Dflt")


def test_no_redundant_override_writes() -> None:
    wez = Wez(data(WEB))
    w = wez.window(1, "/r/web")
    wez.status(w)
    wez.status(w)
    assert w.sets == 1


def test_preview_works_for_a_project_without_a_theme() -> None:
    wez = Wez(data())
    w = wez.window(1, "/r/new/src")
    wez.preview(w, "/r/new", "Dracula")
    assert wez.scheme(w) == "Dracula"
    wez.preview(w, "/r/new", "")
    assert wez.scheme(w) is None


def test_preview_beats_the_saved_scheme_and_clearing_restores_it() -> None:
    wez = Wez(data(WEB))
    w = wez.window(1, "/r/web")
    wez.preview(w, "/r/web", "Dracula")
    assert wez.scheme(w) == "Dracula"
    wez.preview(w, "/r/web", "")
    assert wez.scheme(w) == "Nord"


def test_a_scheme_set_by_someone_else_is_left_alone() -> None:
    wez = Wez(data(WEB))
    w = wez.window(1, "/r/web", scheme="User Light")
    wez.status(w)
    assert wez.scheme(w) == "User Light" and w.sets == 0
    w.overrides.color_scheme = None
    wez.status(w)
    assert wez.scheme(w) == "Nord"


def test_generated_data_with_odd_names_loads() -> None:
    odd = Project(name="odd", path=Path("/r/proj[1]"), theme="x]")
    wez = Wez(data(odd))
    w = wez.window(1, "/r/proj[1]/sub")
    wez.status(w)
    assert wez.scheme(w) == "x]"


def test_malformed_data_entries_are_ignored() -> None:
    wez = Wez(
        "return { projects = { { scheme = 'X' }, 'junk', { path = '/r/web', scheme = 'Nord' } } }"
    )
    w = wez.window(1, "/r/web")
    wez.status(w)
    assert wez.scheme(w) == "Nord"


def test_missing_data_module_is_harmless() -> None:
    wez = Wez(None)
    w = wez.window(1, "/r/web")
    wez.status(w)
    assert wez.scheme(w) is None


def test_focus_request_for_a_closed_pane_is_harmless() -> None:
    wez = Wez(data(WEB))
    w = wez.window(1, "/r/web")
    wez.g.fire("user-var-changed", w, None, "cardak_focus", "42:7")


FOCUS_HEADER = "# cardak-focus: 1"


@pytest.fixture
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    (tmp_path / "cardak").mkdir()
    return tmp_path / "cardak" / "focus"


def focus(wez: Wez, w: Any, focused: bool, ms: int, event: str = "update-status") -> None:
    w.focused = focused
    wez.g.NOW_MS = str(ms)
    wez.g.fire(event, w, None)


def test_focus_written_on_change(state: Path) -> None:
    wez = Wez(data())
    a = wez.window(1, "/r")
    focus(wez, a, True, 1000)
    assert state.read_text().splitlines() == [FOCUS_HEADER, "10 1000"]
    mtime = state.stat().st_mtime_ns
    focus(wez, a, True, 2000)  # no change: no rewrite
    assert state.stat().st_mtime_ns == mtime
    a.pane = 11  # another pane in the same window became active
    focus(wez, a, True, 3000)
    assert state.read_text().splitlines()[1:] == ["10 1000", "11 3000"]


def test_focus_none_when_unfocused(state: Path) -> None:
    wez = Wez(data())
    a, b = wez.window(1, "/r"), wez.window(2, "/r")
    focus(wez, a, True, 1000, "window-focus-changed")
    focus(wez, b, False, 1500)  # some other unfocused window: nothing to say
    focus(wez, a, False, 2000, "window-focus-changed")
    focus(wez, b, True, 2100, "window-focus-changed")
    assert state.read_text().splitlines()[1:] == ["10 1000", "none 2000", "20 2100"]


def test_history_capped_at_64(state: Path) -> None:
    wez = Wez(data())
    a = wez.window(1, "/r")
    for i in range(70):
        a.pane = i
        focus(wez, a, True, 1000 + i)
    lines = state.read_text().splitlines()
    assert lines[0] == FOCUS_HEADER and len(lines) == 65
    assert lines[1] == "6 1006" and lines[-1] == "69 1069"


def test_history_seeded_from_existing_file(state: Path) -> None:
    state.write_text(f"{FOCUS_HEADER}\n5 500\nnone 600\n")
    wez = Wez(data())
    a = wez.window(1, "/r")
    focus(wez, a, True, 1000)
    assert state.read_text().splitlines()[1:] == ["5 500", "none 600", "10 1000"]


def test_focus_is_harmless_without_a_state_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "missing"))
    wez = Wez(data(WEB))
    a = wez.window(1, "/r/web")
    focus(wez, a, True, 1000)
    assert wez.scheme(a) == "Nord"  # theming still ran
    assert not (tmp_path / "missing").exists()


def test_module_version_3() -> None:
    assert MODULE.splitlines()[1] == "-- cardak-module: 3"
