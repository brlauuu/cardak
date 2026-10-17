# /// script
# requires-python = ">=3.12"
# dependencies = ["python-xlib"]
# ///
"""Spike: raise+focus a pane via the Lua user-var trick (no X11 window ids needed).

Usage: uv run spikes/02b_focus_via_uservar.py PANE_ID
Writes OSC 1337 SetUserVar to the pane's tty; the `user-var-changed` handler in
cardak_projects.lua calls pane:activate() + window:focus(). Needs the spike config loaded.
"""

import base64
import json
import subprocess
import sys
import time

from Xlib import X, display

d = display.Display()
root = d.screen().root


def active_title() -> str:
    wid = root.get_full_property(d.intern_atom("_NET_ACTIVE_WINDOW"), X.AnyPropertyType).value[0]
    name = d.create_resource_object("window", wid).get_full_property(
        d.intern_atom("_NET_WM_NAME"), d.intern_atom("UTF8_STRING"))
    return name.value.decode() if name else "?"


def main() -> None:
    pane_id = int(sys.argv[1])
    panes = json.loads(subprocess.run(["wezterm", "cli", "--no-auto-start", "list", "--format", "json"],
                                      capture_output=True, text=True, check=True).stdout)
    pane = next(p for p in panes if p["pane_id"] == pane_id)
    print("before:", active_title())
    value = base64.b64encode(str(time.time()).encode()).decode()  # nonce: the event only fires on change
    with open(pane["tty_name"], "w") as tty:
        tty.write(f"\033]1337;SetUserVar=cardak_focus={value}\007")
    time.sleep(0.7)
    after = active_title()
    print("after :", after)
    print(f"RESULT user-var focus: {'RAISED+FOCUSED' if after == pane['title'] else 'NOT focused'} (target {pane['title']!r})")


if __name__ == "__main__":
    main()
