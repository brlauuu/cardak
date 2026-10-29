from pathlib import Path

ROOT = Path(__file__).parent.parent


def test_packs_are_ignored() -> None:
    # the user's sound packs aren't theirs to publish: never in a commit (#91)
    lines = (ROOT / ".gitignore").read_text().splitlines()
    assert "src/cardak/sounds/packs/" in lines


def test_packs_are_left_out_of_builds() -> None:
    text = (ROOT / "pyproject.toml").read_text()
    assert 'source-exclude = ["cardak/sounds/packs"]' in text
    assert 'wheel-exclude = ["cardak/sounds/packs"]' in text
