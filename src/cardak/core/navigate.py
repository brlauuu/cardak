"""Jump to a pane: select it via `wezterm cli`, then ask cardak.lua to raise its window.

The raise request is an OSC 1337 user var written to the deck's *own* terminal; the
Lua handler looks the target up by id. Nothing is written into other panes.
"""

import base64
import itertools
import json
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


PREVIEW_VAR = "cardak_preview"


def _user_var(name: str, value: str) -> bytes:
    encoded = base64.b64encode(value.encode()).decode()
    return f"\x1b]1337;SetUserVar={name}={encoded}\x07".encode()


def preview_request(project_path: Path, scheme: str, nonce: str) -> bytes:
    return _user_var(
        PREVIEW_VAR, json.dumps({"path": str(project_path), "scheme": scheme, "n": nonce})
    )


def focus_request(pane_id: int, nonce: str) -> bytes:
    return _user_var(FOCUS_VAR, f"{pane_id}:{nonce}")


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


class Previewer:
    """Asks cardak.lua to preview a scheme on a project's windows ("" clears the preview)."""

    def __init__(self, write_tty: Callable[[bytes], None], in_wezterm: bool) -> None:
        self.write_tty = write_tty
        self.in_wezterm = in_wezterm
        self._counter = itertools.count()

    @classmethod
    def live(cls, env: Mapping[str, str] | None = None) -> "Previewer":
        env = os.environ if env is None else env
        return cls(_write_dev_tty, "WEZTERM_PANE" in env)

    def preview(self, project_path: Path, scheme: str) -> bool:
        if not self.in_wezterm:
            return False
        nonce = f"{time.time_ns()}.{next(self._counter)}"
        try:
            self.write_tty(preview_request(project_path, scheme, nonce))
        except OSError:
            return False
        return True
