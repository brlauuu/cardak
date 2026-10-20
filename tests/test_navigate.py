import base64
import json
from pathlib import Path

from cardak.collectors.wezterm import WeztermUnavailable
from cardak.core.navigate import (
    JumpResult,
    Navigator,
    Previewer,
    focus_request,
    preview_request,
)


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
    assert (
        first.message == "Selected pane 4. Run `cardak wezterm install` to also raise its window."
    )
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


def test_preview_request_bytes() -> None:
    data = preview_request(Path("/r/web"), "Nord", "7")
    prefix = b"\x1b]1337;SetUserVar=cardak_preview="
    assert data.startswith(prefix) and data.endswith(b"\x07")
    assert json.loads(base64.b64decode(data[len(prefix) : -1])) == {
        "path": "/r/web",
        "scheme": "Nord",
        "n": "7",
    }


def test_previewer() -> None:
    rec = Recorder()
    p = Previewer(rec.write, in_wezterm=True)
    assert p.preview(Path("/r/web"), "Nord")
    assert p.preview(Path("/r/web"), "")
    assert len(rec.written) == 2 and rec.written[0] != rec.written[1]
    assert not Previewer(rec.write, in_wezterm=False).preview(Path("/r"), "Nord")
    assert not Previewer(Recorder(fail_write="x").write, in_wezterm=True).preview(Path("/r"), "N")
    assert Previewer.live({"WEZTERM_PANE": "1"}).in_wezterm
