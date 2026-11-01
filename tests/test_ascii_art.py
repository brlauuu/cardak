from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from cardak.core.ascii_art import MAX_COLS, MAX_ROWS, ArtError, load_image, logo_art, name_art

FIXTURES = Path(__file__).parent / "fixtures"


def test_name_art_is_figlet() -> None:
    art = name_art("cardak")
    assert len(art.splitlines()) == 5
    assert "\\___" in art
    assert all(line == line.rstrip() for line in art.splitlines())


def test_name_art_folds_accents() -> None:
    assert name_art("Čardak") == name_art("Cardak")
    assert name_art("Đorđe") == name_art("Dorde")


def test_name_art_with_no_drawable_letters() -> None:
    assert name_art("日本") == "日本"


def test_logo_art_of_an_svg_fits_the_cap() -> None:
    art = logo_art(load_image(FIXTURES / "logos" / "mark.svg"))
    lines = art.splitlines()
    assert 0 < len(lines) <= MAX_ROWS
    assert max(map(len, lines)) <= MAX_COLS
    assert "@" in art


def test_logo_art_of_an_opaque_png(tmp_path: Path) -> None:
    img = Image.new("RGB", (200, 100), "white")
    ImageDraw.Draw(img).rectangle((80, 30, 120, 70), fill="black")
    img.save(tmp_path / "x.png")
    lines = logo_art(load_image(tmp_path / "x.png")).splitlines()
    assert "@" in lines[len(lines) // 2]  # cropped to the square: it fills the art


def test_a_wordmark_is_refused(tmp_path: Path) -> None:
    # too wide to read in a few rows: the name's own letters say it better
    img = Image.new("RGBA", (420, 70))
    ImageDraw.Draw(img).rectangle((10, 10, 409, 59), fill=(255, 0, 0, 255))
    img.save(tmp_path / "bar.png")
    with pytest.raises(ArtError, match="wordmark"):
        logo_art(load_image(tmp_path / "bar.png"))


def test_a_tile_shows_the_mark_inside(tmp_path: Path) -> None:
    img = Image.new("RGBA", (200, 200))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((0, 0, 199, 199), radius=40, fill=(30, 120, 60, 255))
    draw.ellipse((70, 70, 130, 130), fill=(255, 255, 255, 255))
    img.save(tmp_path / "tile.png")
    lines = logo_art(load_image(tmp_path / "tile.png")).splitlines()
    assert "@" in lines[len(lines) // 2]
    assert lines[0].startswith(" " * 6)  # the circle's round top, not the tile's square edge


def test_tall_logo_is_capped_by_rows(tmp_path: Path) -> None:
    img = Image.new("RGBA", (70, 420))
    ImageDraw.Draw(img).rectangle((10, 10, 59, 409), fill=(0, 0, 0, 255))
    img.save(tmp_path / "pole.png")
    lines = logo_art(load_image(tmp_path / "pole.png")).splitlines()
    assert len(lines) == MAX_ROWS
    assert max(map(len, lines)) <= 4


def test_blank_image_raises(tmp_path: Path) -> None:
    Image.new("RGBA", (40, 40)).save(tmp_path / "blank.png")
    with pytest.raises(ArtError):
        logo_art(load_image(tmp_path / "blank.png"))


@pytest.mark.parametrize("name", ["x.png", "x.svg", "x.ico"])
def test_load_image_rejects_garbage(tmp_path: Path, name: str) -> None:
    (tmp_path / name).write_bytes(b"not an image")
    with pytest.raises(ArtError):
        load_image(tmp_path / name)


def test_load_image_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ArtError):
        load_image(tmp_path / "gone.png")
