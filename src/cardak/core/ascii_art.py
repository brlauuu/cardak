"""A project's ASCII art (#9): its name in figlet letters, or its logo in characters.

Pillow, pyfiglet and resvg are imported inside the functions: only the code that makes art
loads them.
"""

import io
import unicodedata
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL.Image import Image

MAX_COLS = 40
MAX_ROWS = 16
MIN_ROWS = 8  # fewer: a wordmark, unreadable in characters; the name's letters say it better
TILE = 0.85  # an opaque shape filling this much of its box is a tile with the mark inside
RAMP = " .:-=+*#%@"  # sparse to dense
SVG_WIDTH = 320  # pixels an SVG is drawn at before it's cut into cells
INK = 8  # below this (of 255) a pixel is background
_NO_DECOMPOSITION = str.maketrans(
    {"Đ": "D", "đ": "d", "Ø": "O", "ø": "o", "Ł": "L", "ł": "l", "ß": "ss", "Æ": "AE", "æ": "ae"}
)


_THRESHOLD = [0] * (INK + 1) + [255] * (255 - INK)


class ArtError(Exception):
    pass


def _ascii(name: str) -> str:
    folded = unicodedata.normalize("NFKD", name.translate(_NO_DECOMPOSITION))
    return "".join(c for c in folded if c.isascii() and c.isprintable())


def _trimmed(text: str) -> str:
    lines = [line.rstrip() for line in text.splitlines()]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def name_art(name: str) -> str:
    """The name in figlet's standard letters; the name itself when no letter can be drawn."""
    import pyfiglet

    text = _ascii(name).strip()
    art = _trimmed(pyfiglet.figlet_format(text, font="standard", width=1000)) if text else ""
    return art or name


def load_image(path: Path) -> "Image":
    """An image file as RGBA; an SVG is drawn first. Any failure is an ArtError."""
    from PIL import Image as PilImage

    try:
        if path.suffix.lower() == ".svg":
            import resvg_py

            data = bytes(resvg_py.svg_to_bytes(svg_path=str(path), width=SVG_WIDTH))
            image = PilImage.open(io.BytesIO(data))
        else:
            image = PilImage.open(path)
        image.load()
        return image.convert("RGBA")
    except Exception as e:  # Pillow and resvg raise many kinds; any of them means no logo
        raise ArtError(f"{path.name}: {e}") from e


def _distance(gray: "Image", value: int) -> "Image":
    """How far each pixel's gray is from value, 0–255."""
    return gray.point([abs(v - value) for v in range(256)])


def _ink(image: "Image") -> "Image":
    """Where the logo is, 0–255: its alpha when it has any, else how far from the corner's
    color. A tile (an opaque rounded square, an app icon) is the mark's backdrop: inside it, the
    ink is how far from the tile's own color."""
    from PIL import ImageChops, ImageStat

    alpha = image.getchannel("A")
    low, _ = alpha.getextrema()
    gray = image.convert("L")
    if not (isinstance(low, int) and low < 250):
        corner = gray.getpixel((0, 0))
        return _distance(gray, corner if isinstance(corner, int) else 0)
    box = alpha.getbbox()
    if box is None or ImageStat.Stat(alpha.crop(box)).mean[0] < 255 * TILE:
        return alpha
    solid = alpha.point([0] * 251 + [255] * 5)
    histogram = gray.histogram(mask=solid)
    tile = max(range(256), key=histogram.__getitem__)
    inside = ImageChops.multiply(_distance(gray, tile), solid)
    return inside if inside.point(_THRESHOLD).getbbox() else alpha  # one flat color: the shape


def _size(width: int, height: int) -> tuple[int, int]:
    """Columns and rows for a width × height image; a cell is about twice as tall as wide."""
    cols = MAX_COLS
    rows = max(1, round(cols * height / width / 2))
    if rows > MAX_ROWS:
        rows = MAX_ROWS
        cols = max(1, round(rows * 2 * width / height))
    return cols, rows


def logo_art(image: "Image") -> str:
    """The logo in RAMP's characters, at most MAX_COLS × MAX_ROWS, cropped to the mark."""
    from PIL import Image as PilImage

    ink = _ink(image)
    box = ink.point(_THRESHOLD).getbbox()
    if box is None:
        raise ArtError("blank image")
    ink = ink.crop(box)
    _, high = ink.getextrema()
    if isinstance(high, int) and 0 < high < 255:  # the strongest ink is the densest character
        ink = ink.point([min(255, v * 255 // high) for v in range(256)])
    cols, rows = _size(*ink.size)
    if rows < MIN_ROWS:
        raise ArtError("too wide: a wordmark")
    small = ink.resize((cols, rows), PilImage.Resampling.BOX)
    steps = len(RAMP) - 1
    lines = []
    for y in range(rows):
        values = (small.getpixel((x, y)) for x in range(cols))
        lines.append("".join(RAMP[v * steps // 255 if isinstance(v, int) else 0] for v in values))
    art = _trimmed("\n".join(lines))
    if not art:
        raise ArtError("blank image")
    return art
