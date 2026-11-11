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


def test_site_is_not_packaged() -> None:
    # the landing page (#66) lives next to src/, never in the wheel or sdist
    text = (ROOT / "pyproject.toml").read_text()
    for key in ("source-include", "wheel-include"):
        for line in text.splitlines():
            assert not (line.startswith(key) and "site" in line)


def test_extras_and_a_slim_base() -> None:
    # #131: the art's and Anthropic's libraries are extras; [all] is the documented install
    import tomllib

    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    extras = project["optional-dependencies"]
    assert set(extras) == {"background", "anthropic", "all"}
    assert extras["all"] == ["cardak[background,anthropic]"]
    names = {d.split(">")[0].split("=")[0] for group in extras.values() for d in group}
    base = {d.split(">")[0].split("=")[0] for d in project["dependencies"]}
    assert not base & names
    assert {"pillow", "pyfiglet", "resvg-py", "anthropic"} <= names
