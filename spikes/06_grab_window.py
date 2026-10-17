# /// script
# requires-python = ">=3.12"
# dependencies = ["python-xlib", "pillow"]
# ///
"""Capture a WezTerm X11 window by title without focusing it (works when obscured under compositing).

Usage: uv run spikes/06_grab_window.py TITLE OUT.png
Prints the background color sampled near the bottom-left and bottom-right of the window.
"""

import sys

from PIL import Image
from Xlib import X, display

d = display.Display()
root = d.screen().root


def find(title: str):
    for wid in root.get_full_property(d.intern_atom("_NET_CLIENT_LIST"), X.AnyPropertyType).value:
        w = d.create_resource_object("window", wid)
        name = w.get_full_property(d.intern_atom("_NET_WM_NAME"), d.intern_atom("UTF8_STRING"))
        if name and name.value.decode() == title and "wezterm" in (w.get_wm_class() or ("", ""))[1]:
            return w
    raise SystemExit(f"no wezterm window titled {title!r}")


def main() -> None:
    w = find(sys.argv[1])
    g = w.get_geometry()
    raw = w.get_image(0, 0, g.width, g.height, X.ZPixmap, 0xFFFFFFFF)
    img = Image.frombytes("RGB", (g.width, g.height), raw.data, "raw", "BGRX")
    img.save(sys.argv[2])
    for label, x in (("left ", 10), ("right", g.width - 10)):
        print(f"{label} bg: #{'%02x%02x%02x' % img.getpixel((x, g.height - 10))}")


if __name__ == "__main__":
    main()
