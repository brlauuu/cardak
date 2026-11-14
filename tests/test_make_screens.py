"""The landing page's screens come from invented data, never from this machine (#106)."""

import getpass
import importlib.util
from itertools import combinations
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent

_spec = importlib.util.spec_from_file_location("make_screens", ROOT / "scripts" / "make_screens.py")
assert _spec and _spec.loader
make_screens = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(make_screens)

SLIDES = {
    "overview": "api-gateway",
    "waiting": "npm run test:e2e",  # the waiting session's details
    "summaries": "the 2025 holdout or a new 10%",
    "usage": "cost",
    "board": "Coupon codes are lost",
    "colors": "Tokyo Night",
}


@pytest.fixture(scope="module")
def svgs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    made = make_screens.deck_svgs(tmp_path_factory.mktemp("svg"))
    return {name: path.read_text() for name, path in made.items()}


def _text(svg: str) -> str:
    # Textual's SVG puts each run of text in its own element, with spaces as &#160;
    import re

    return re.sub(r"<[^>]+>", "", svg).replace("&#160;", " ")


def test_deck_svgs_make_every_slide(svgs: dict[str, str]) -> None:
    assert set(svgs) == set(SLIDES)
    for name, marker in SLIDES.items():
        assert marker in _text(svgs[name]), name


def test_nothing_of_this_machine(svgs: dict[str, str]) -> None:
    for name, svg in svgs.items():
        for leak in (str(Path.home()), getpass.getuser(), "brlauuu"):
            assert leak not in svg, (name, leak)


def test_slides_differ(svgs: dict[str, str]) -> None:
    for a, b in combinations(svgs, 2):
        assert _text(svgs[a]) != _text(svgs[b]), (a, b)


def test_the_phone_slide_reads_the_same_snapshot() -> None:
    snap = make_screens.demo_snapshot(1_000_000.0, summaries=True)
    assert snap.n_waiting == 3
    assert all(r.cwd and r.cwd.is_relative_to("/home/demo") for g in snap.groups for r in g.panes)
