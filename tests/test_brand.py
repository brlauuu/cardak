import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
ASSETS = ROOT / "site" / "assets"
SVG_NS = "{http://www.w3.org/2000/svg}"

# Danube night, paprika red, linen, and graphite for the one-ink mark (#66)
PALETTE = {"#0f2a44", "#c8352b", "#f6efe2", "#17181c"}
HACEK = "M38 14 L50 33 L62 14"
MARKS = ["cardak-mark-light.svg", "cardak-mark-dark.svg", "cardak-mark-mono.svg", "cardak-icon.svg"]
WORDMARKS = ["cardak-wordmark-light.svg", "cardak-wordmark-dark.svg"]
SVGS = MARKS + WORDMARKS


def _root(name: str) -> ET.Element:
    return ET.parse(ASSETS / name).getroot()


def _colors(text: str) -> set[str]:
    return {c.lower() for c in re.findall(r"#[0-9a-fA-F]{6}\b", text)}


@pytest.mark.parametrize("name", SVGS)
def test_svg_parses_and_has_a_view_box(name: str) -> None:
    root = _root(name)
    assert root.tag == f"{SVG_NS}svg"
    assert root.get("viewBox") == ("0 0 460 100" if name in WORDMARKS else "0 0 100 100")


@pytest.mark.parametrize("name", SVGS)
def test_svg_uses_only_the_palette(name: str) -> None:
    text = (ASSETS / name).read_text()
    assert _colors(text) <= PALETTE
    # black and white only paint the mask that cuts the háček out of the C
    outside = re.sub(r"<mask.*?</mask>", "", text, flags=re.S)
    assert not re.search(r"#(fff|000)\b|\b(white|black)\b", outside, flags=re.I)


@pytest.mark.parametrize("name", SVGS)
def test_svg_has_no_editor_metadata(name: str) -> None:
    text = (ASSETS / name).read_text().lower()
    for word in ("inkscape", "sodipodi", "<metadata", "data:"):
        assert word not in text


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
