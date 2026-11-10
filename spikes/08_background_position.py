# /// script
# requires-python = ">=3.12"
# dependencies = ["python-xlib", "pillow"]
# ///
"""#132: where does a Right/Top-aligned background layer land for a given
horizontal_offset / vertical_offset, and what happens to the part outside the window?

Usage: uv run spikes/08_background_position.py OUT_DIR
Starts a separate WezTerm GUI per case (--always-new-process, scratch XDG dirs), grabs its
window by _NET_WM_PID without focusing it, and prints the art's bounding box in pixels and
in cells from the window's top-right corner.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image, ImageChops
from Xlib import X, display

LUA = """
local wezterm = require 'wezterm'
local config = wezterm.config_builder()
config.color_scheme = 'GoogleDark (Gogh)'
config.initial_cols, config.initial_rows = 80, 24
config.enable_tab_bar = false
config.window_padding = { left = 0, right = 0, top = 0, bottom = 0 }
config.window_decorations = 'NONE'
local scheme = wezterm.color.get_builtin_schemes()['GoogleDark (Gogh)']
wezterm.on('update-status', function(window)
  local o = window:get_config_overrides() or {}
  if o.background then return end
  o.background = {
    { source = { Color = scheme.background }, width = '100%', height = '100%' },
    { source = { File = IMAGE }, repeat_x = 'NoRepeat', repeat_y = 'NoRepeat',
      horizontal_align = 'Right', vertical_align = 'Top', opacity = 1.0,
      width = '10cell', height = '4cell', horizontal_offset = HOFF, vertical_offset = VOFF },
  }
  window:set_config_overrides(o)
end)
return config
"""

CASES = [
    ("zero", "'0cell'", "'0cell'"),
    ("neg", "'-2cell'", "'1cell'"),
    ("pos", "'2cell'", "'1cell'"),
    ("left_out", "'-75cell'", "'0cell'"),
    ("down_out", "'0cell'", "'22cell'"),
]


def grab(pid: int) -> Image.Image:
    d = display.Display()
    root = d.screen().root
    for wid in root.get_full_property(d.intern_atom("_NET_CLIENT_LIST"), X.AnyPropertyType).value:
        w = d.create_resource_object("window", wid)
        p = w.get_full_property(d.intern_atom("_NET_WM_PID"), X.AnyPropertyType)
        if p and p.value[0] == pid:
            g = w.get_geometry()
            raw = w.get_image(0, 0, g.width, g.height, X.ZPixmap, 0xFFFFFFFF)
            return Image.frombytes("RGB", (g.width, g.height), raw.data, "raw", "BGRX")
    raise SystemExit(f"no window for pid {pid}")


def main() -> None:
    out = Path(sys.argv[1]).resolve()
    out.mkdir(parents=True, exist_ok=True)
    art = out / "art.png"
    Image.new("RGB", (100, 40), "#ff00ff").save(art)  # a solid block: its box is the layer's
    for name, hoff, voff in CASES:
        cfg = out / f"{name}.lua"
        cfg.write_text(
            LUA.replace("IMAGE", repr(str(art))).replace("HOFF", hoff).replace("VOFF", voff)
        )
        env = {**os.environ, "XDG_CONFIG_HOME": str(out / "cfg"), "XDG_CACHE_HOME": str(out / "cache")}
        gui = subprocess.Popen(
            ["wezterm", "--config-file", str(cfg), "start", "--always-new-process", "--",
             "sh", "-c", "printf '\\033[?25l'; sleep 30"],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(4)
        shot = grab(gui.pid)
        gui.terminate()
        gui.wait()
        shot.save(out / f"{name}.png")
        w, h = shot.size
        cw, ch = w / 80, h / 24
        magenta = Image.new("RGB", shot.size, "#ff00ff")
        diff = ImageChops.difference(shot, magenta).convert("L").point(lambda v: 255 if v < 40 else 0)
        box = diff.getbbox()
        if box is None:
            print(f"{name:9} h={hoff} v={voff}: art not visible")
            continue
        l, t, r, b = box
        print(
            f"{name:9} h={hoff} v={voff}: px {box} of {w}x{h}; "
            f"right edge {(w - r) / cw:.1f} cells from right, top {t / ch:.1f} rows down, "
            f"size {(r - l) / cw:.1f}x{(b - t) / ch:.1f} cells"
        )


if __name__ == "__main__":
    main()
