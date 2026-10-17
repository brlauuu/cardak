# /// script
# requires-python = ">=3.12"
# dependencies = ["python-xlib"]
# ///
"""Spike: does `wezterm cli activate-pane` raise + focus a pane in another window (KDE, X11)?

Usage: uv run spikes/02_activate_pane.py PANE_ID [--fallback]

Reports the X11 active window before and after. With --fallback, if activate-pane
did not focus the window, sends an EWMH _NET_ACTIVE_WINDOW request with source=2
("pager"), which KWin honours without focus-stealing prevention. No system packages needed.
"""

import json
import subprocess
import sys
import time

from Xlib import X, display, protocol

d = display.Display()
root = d.screen().root
A = d.intern_atom


def active_window():
    wid = root.get_full_property(A("_NET_ACTIVE_WINDOW"), X.AnyPropertyType).value[0]
    return d.create_resource_object("window", wid)


def describe(w) -> str:
    name = w.get_full_property(A("_NET_WM_NAME"), A("UTF8_STRING"))
    cls = w.get_wm_class()
    return f"0x{w.id:x} class={cls[1] if cls else '?'} title={name.value.decode() if name else '?'!r}"


def wezterm(*args: str) -> str:
    return subprocess.run(["wezterm", "cli", *args], capture_output=True, text=True, check=True).stdout


def x_window_for_title(title: str):
    for wid in root.get_full_property(A("_NET_CLIENT_LIST"), X.AnyPropertyType).value:
        w = d.create_resource_object("window", wid)
        name = w.get_full_property(A("_NET_WM_NAME"), A("UTF8_STRING"))
        if name and name.value.decode() == title:
            return w
    return None


def ewmh_activate(w) -> None:
    ev = protocol.event.ClientMessage(window=w, client_type=A("_NET_ACTIVE_WINDOW"),
                                      data=(32, [2, X.CurrentTime, 0, 0, 0]))
    root.send_event(ev, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
    d.flush()


def main() -> None:
    pane_id = int(sys.argv[1])
    pane = next(p for p in json.loads(wezterm("list", "--format", "json")) if p["pane_id"] == pane_id)
    # The X11 window title is the active pane's title (`set-window-title` does not change it),
    # so find the target X window by pane title. Fine for a spike where titles are unique.
    marker = pane["title"]

    print("before :", describe(active_window()))
    wezterm("activate-pane", "--pane-id", str(pane_id))
    time.sleep(0.7)
    after = active_window()
    print("after  :", describe(after))
    focused = [c["focused_pane_id"] for c in json.loads(wezterm("list-clients", "--format", "json"))]
    print("wezterm focused_pane_id:", focused)
    target = x_window_for_title(marker)
    ok = target is not None and after.id == target.id
    print(f"RESULT activate-pane: {'RAISED+FOCUSED' if ok else 'NOT focused'} (target X window {target and hex(target.id)})")

    if not ok and "--fallback" in sys.argv and target is not None:
        ewmh_activate(target)
        time.sleep(0.7)
        after = active_window()
        print("after fallback:", describe(after))
        print(f"RESULT EWMH fallback: {'RAISED+FOCUSED' if after.id == target.id else 'NOT focused'}")


if __name__ == "__main__":
    main()
