"""Render the PNG icons in site/assets/ from the logo SVGs next to them (#66).

Headless Chrome draws each SVG at its size on a transparent background. Re-run after
changing a logo SVG: `uv run scripts/make_brand_pngs.py`. It also draws the 1200×630 link
preview, `cardak-og.png` (#106): the wordmark and the tagline on Danube night.
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ASSETS = Path(__file__).resolve().parent.parent / "site" / "assets"

# (source SVG, PNG written, size in px, background as RRGGBBAA); the icon renders in its
# default, dark state. iOS rounds the 180 px home-screen icon itself and turns transparent
# corners black, so that one gets a square of the tile's color behind it.
PNGS = [
    ("cardak-icon.svg", "cardak-icon-32.png", 32, "00000000"),
    ("cardak-icon.svg", "cardak-icon-180.png", 180, "0f2a44ff"),
    ("cardak-mark-mono.svg", "cardak-mark-mono-512.png", 512, "00000000"),
]


def render(chrome: str, svg: Path, png: Path, size: int, background: str, scratch: Path) -> None:
    page = scratch / f"{png.stem}.html"
    page.write_text(
        f'<body style="margin:0;background:transparent">'
        f'<img src="{svg.as_uri()}" width="{size}" height="{size}" style="display:block">'
    )
    subprocess.run(
        [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--allow-file-access-from-files",
            "--blink-settings=preferredColorScheme=0",
            f"--default-background-color={background}",
            f"--window-size={size},{size}",
            f"--screenshot={png}",
            page.as_uri(),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )


TAGLINE = "Your control deck for multi-project agentic development."
OG = (1200, 630)


def render_og(chrome: str, png: Path, scratch: Path) -> None:
    width, height = OG
    wordmark = (ASSETS / "cardak-wordmark-dark.svg").as_uri()
    page = scratch / "og.html"
    page.write_text(
        f'<body style="margin:0;width:{width}px;height:{height}px;background:#0f2a44;'
        "display:flex;flex-direction:column;align-items:center;justify-content:center;gap:44px;"
        "font-family:system-ui,Ubuntu,'Noto Sans',sans-serif\">"
        f'<img src="{wordmark}" width="600" style="display:block">'
        f'<p style="margin:0;color:#f6efe2;font-size:34px;letter-spacing:.01em">{TAGLINE}</p>'
    )
    subprocess.run(
        [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--allow-file-access-from-files",
            f"--window-size={width},{height}",
            f"--screenshot={png}",
            page.as_uri(),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )


def main() -> None:
    chrome = shutil.which("google-chrome") or shutil.which("chromium")
    if chrome is None:
        sys.exit("make_brand_pngs: needs google-chrome or chromium on PATH")
    with tempfile.TemporaryDirectory() as scratch:
        for svg, png, size, background in PNGS:
            render(chrome, ASSETS / svg, ASSETS / png, size, background, Path(scratch))
            print(f"wrote {png} ({size} px)")
        render_og(chrome, ASSETS / "cardak-og.png", Path(scratch))
        print(f"wrote cardak-og.png ({OG[0]}×{OG[1]})")


if __name__ == "__main__":
    main()
