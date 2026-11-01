# /// script
# requires-python = ">=3.12"
# dependencies = ["python-xlib", "pillow", "pyfiglet", "resvg-py"]
# ///
"""#9: does a corner background image at low opacity work when set through
window:set_config_overrides, and what does it do to an OSC 11 pane tint?

Usage: uv run spikes/07_background.py OUT_DIR [LOGO.svg]
Starts a separate WezTerm GUI (--always-new-process, scratch XDG dirs), grabs its window by
_NET_WM_PID without focusing it, and saves before/after screenshots.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import pyfiglet
from PIL import Image, ImageDraw, ImageFont
from Xlib import X, display

FONT = subprocess.run(
    ["fc-match", "-f", "%{file}", "monospace"], capture_output=True, text=True
).stdout


def render(text: str, color: str, out: Path) -> None:
    font = ImageFont.truetype(FONT, 28)
    lines = text.splitlines()
    left, top, right, bottom = font.getbbox("M")
    cw, ch = font.getlength("M"), int((bottom - top) * 1.25) + 2
    img = Image.new("RGBA", (int(cw * max(map(len, lines))) + 8, ch * len(lines) + 8))
    draw = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        draw.text((4, 4 + i * ch), line, font=font, fill=color)
    img.save(out)


def svg_ascii(svg: Path, cols: int = 40, rows: int = 16) -> str:
    import io

    import resvg_py

    png = bytes(resvg_py.svg_to_bytes(svg_path=str(svg), width=cols * 8))
    im = Image.open(io.BytesIO(png)).convert("RGBA")
    bg = Image.new("RGBA", im.size, (0, 0, 0, 255))
    im = Image.alpha_composite(bg, im).convert("L").resize((cols, rows))
    ramp = " .:-=+*#%@"
    return "\n".join(
        "".join(ramp[im.getpixel((x, y)) * (len(ramp) - 1) // 255] for x in range(cols))
        for y in range(rows)
    )


LUA = """
local wezterm = require 'wezterm'
local config = wezterm.config_builder()
config.color_scheme = 'GoogleDark (Gogh)'
config.initial_cols, config.initial_rows = 100, 30
config.enable_tab_bar = true
local scheme = wezterm.color.get_builtin_schemes()['GoogleDark (Gogh)']
wezterm.on('update-status', function(window)
  local o = window:get_config_overrides() or {}
  if o.background then return end
  o.background = {
    { source = { Color = scheme.background }, width = '100%', height = '100%' },
    { source = { File = IMAGE }, repeat_x = 'NoRepeat', repeat_y = 'NoRepeat',
      horizontal_align = 'Right', vertical_align = 'Bottom', opacity = OPACITY,
      width = WIDTH, height = HEIGHT, horizontal_offset = '-1cell', vertical_offset = '-1cell' },
  }
  window:set_config_overrides(o)
end)
return config
"""


def grab(pid: int, out: Path) -> None:
    d = display.Display()
    root = d.screen().root
    for wid in root.get_full_property(d.intern_atom("_NET_CLIENT_LIST"), X.AnyPropertyType).value:
        w = d.create_resource_object("window", wid)
        p = w.get_full_property(d.intern_atom("_NET_WM_PID"), X.AnyPropertyType)
        if p and p.value[0] == pid:
            g = w.get_geometry()
            raw = w.get_image(0, 0, g.width, g.height, X.ZPixmap, 0xFFFFFFFF)
            Image.frombytes("RGB", (g.width, g.height), raw.data, "raw", "BGRX").save(out)
            return
    raise SystemExit(f"no window for pid {pid}")


def main() -> None:
    out = Path(sys.argv[1]).resolve()
    out.mkdir(parents=True, exist_ok=True)
    art = pyfiglet.figlet_format("cardak", font="standard")
    if len(sys.argv) > 2:
        art = svg_ascii(Path(sys.argv[2]))
    (out / "art.txt").write_text(art)
    render(art, "#e8eaed", out / "art.png")
    for name, opacity, tint in (("plain", 0.06, False), ("strong", 0.2, False), ("tint", 0.2, True)):
        cfg = out / f"{name}.lua"
        cfg.write_text(
            LUA.replace("IMAGE", repr(str(out / "art.png"))).replace("OPACITY", str(opacity)).replace("WIDTH", repr(f"{max(map(len, art.splitlines()))}cell")).replace("HEIGHT", repr(f"{len(art.splitlines())}cell"))
        )
        env = {**os.environ, "XDG_CONFIG_HOME": str(out / "cfg"), "XDG_CACHE_HOME": str(out / "cache")}
        cmd = "ls -la /usr/lib | head -40"
        if tint:
            cmd = "printf '\\033]11;#402020\\007'; " + cmd
        gui = subprocess.Popen(
            ["wezterm", "--config-file", str(cfg), "start", "--always-new-process", "--",
             "sh", "-c", cmd + "; sleep 30"],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(4)
        grab(gui.pid, out / f"{name}.png")
        gui.terminate()
        gui.wait()
    print(out)


if __name__ == "__main__":
    main()
