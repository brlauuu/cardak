import re
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
ASSETS = ROOT / "site" / "assets"
SVG_NS = "{http://www.w3.org/2000/svg}"

# Danube night, paprika red, linen, and graphite for the one-ink mark (#66)
PALETTE = {"#0f2a44", "#c8352b", "#f6efe2", "#17181c"}
HACEK = "M38 14 L50 33 L62 14"
WORDMARKS = ["cardak-wordmark-light.svg", "cardak-wordmark-dark.svg"]
# every SVG there, so one added later is checked too
SVGS = sorted(p.name for p in ASSETS.glob("*.svg"))
PAINT = ("fill", "stroke", "stop-color", "color")
EDITORS = (
    "inkscape",
    "sodipodi",
    "<metadata",
    "data:",
    "generator",
    "illustrator",
    "sketch",
    "figma",
    "<desc",
    "xmlns:",
)


def _root(name: str) -> ET.Element:
    return ET.parse(ASSETS / name).getroot()


def _colors(text: str) -> set[str]:
    return {c.lower() for c in re.findall(r"#[0-9a-fA-F]{6}\b", text)}


def _declarations(css: str) -> list[str]:
    """The values of every paint property in a style sheet or style attribute."""
    props = "|".join(PAINT)
    return [v.strip() for v in re.findall(rf"(?<![\w-])(?:{props})\s*:\s*([^;}}]+)", css)]


def _stray_colors(svg: str) -> list[str]:
    """Paint values outside the palette; black and white may paint only inside a <mask>."""
    root = ET.fromstring(svg)
    masked = {id(e) for mask in root.iter(f"{SVG_NS}mask") for e in mask.iter()}
    values: list[str] = []
    for e in root.iter():
        if e.tag == f"{SVG_NS}style":
            values += _declarations(e.text or "")
        if id(e) in masked:
            continue
        values += [e.get(name, "") for name in PAINT if e.get(name)]
        values += _declarations(e.get("style", ""))
    return [v for v in values if not _allowed(v)]


def _allowed(paint: str) -> bool:
    return paint == "none" or paint.startswith("url(#") or paint.lower() in PALETTE


@pytest.mark.parametrize(
    "paint",
    [
        'stroke="#f00"',
        'stroke="#c8352bcc"',
        'stroke="red"',
        'fill="gray"',
        'stroke="rgb(200,0,0)"',
        'style="stroke: #abcdef"',
    ],
)
def test_stray_colors_are_caught(paint: str) -> None:
    svg = f'<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0" {paint}/></svg>'
    assert _stray_colors(svg)


def test_stray_colors_in_a_style_sheet_are_caught() -> None:
    svg = '<svg xmlns="http://www.w3.org/2000/svg"><style>.c { stroke: teal; }</style></svg>'
    assert _stray_colors(svg) == ["teal"]


def test_palette_paint_is_not_stray() -> None:
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg"><style>.t { fill: #0F2A44; }</style>'
        '<mask id="m"><rect fill="#fff"/></mask>'
        '<path fill="none" stroke="#c8352b" mask="url(#m)" style="stroke: #f6efe2"/></svg>'
    )
    assert _stray_colors(svg) == []


@pytest.mark.parametrize("name", SVGS)
def test_svg_parses_and_has_a_view_box(name: str) -> None:
    root = _root(name)
    assert root.tag == f"{SVG_NS}svg"
    assert root.get("viewBox") == ("0 0 460 100" if name in WORDMARKS else "0 0 100 100")


@pytest.mark.parametrize("name", SVGS)
def test_svg_uses_only_the_palette(name: str) -> None:
    assert _stray_colors((ASSETS / name).read_text()) == []


@pytest.mark.parametrize("name", SVGS)
def test_svg_has_no_editor_metadata(name: str) -> None:
    text = (ASSETS / name).read_text().lower()
    for word in EDITORS:
        assert word not in text, word


@pytest.mark.parametrize("name", SVGS)
def test_the_cut_is_a_mask(name: str) -> None:
    cuts = [
        p
        for mask in _root(name).iter(f"{SVG_NS}mask")
        for p in mask.iter(f"{SVG_NS}path")
        if p.get("d") == HACEK and p.get("stroke-width") == "16"
    ]
    assert len(cuts) == 1


def test_mono_mark_is_one_ink() -> None:
    assert _colors((ASSETS / "cardak-mark-mono.svg").read_text()) == {"#17181c"}


@pytest.mark.parametrize("name", WORDMARKS)
def test_wordmark_letters_follow_the_spec(name: str) -> None:
    paths = [p.get("d") for p in _root(name).iter(f"{SVG_NS}path")]
    for d in (
        "M186 86 V30 M186 58 A28 28 0 0 1 214 30",
        "M298 6 V86",
        "M414 6 V86 M444 30 L414 62 M424 52 L448 86",
    ):
        assert d in paths
    # one háček drawn, one in the mask: only the Č has it
    assert paths.count(HACEK) == 2


PNGS = [("cardak-icon-32.png", 32), ("cardak-icon-180.png", 180), ("cardak-mark-mono-512.png", 512)]


@pytest.mark.parametrize(("name", "size"), PNGS)
def test_png_has_its_size(name: str, size: int) -> None:
    data = (ASSETS / name).read_bytes()
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    assert struct.unpack(">II", data[16:24]) == (size, size)


def test_mono_png_is_transparent() -> None:
    data = (ASSETS / "cardak-mark-mono-512.png").read_bytes()
    assert data[25] == 6  # color type RGBA


@pytest.mark.parametrize(
    ("bundled", "site"), [("icon.svg", "cardak-icon.svg"), ("icon-180.png", "cardak-icon-180.png")]
)
def test_bundled_icons_match_the_site(bundled: str, site: str) -> None:
    # the phone page's icons (#106) are copies the daemon serves; they must not drift
    root = Path(__file__).parent.parent
    assert (root / "src/cardak/web" / bundled).read_bytes() == (
        root / "site/assets" / site
    ).read_bytes()
