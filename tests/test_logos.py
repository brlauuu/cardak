from pathlib import Path

from cardak.collectors.logos import readme_images


def test_readme_images_in_order(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text(
        '<p><img src="apps/x/logo.svg" width=4 alt="X" /></p>\n'
        "![wordmark](site/a.png)\n"
        "![badge](https://img.shields.io/x?logo=python)\n"
        "![abs](/etc/passwd.png)\n"
        "![frag](docs/b.png#light)\n"
        "<img alt='y' src='c.svg?raw=1'>\n"
    )
    assert readme_images(tmp_path) == ("apps/x/logo.svg", "site/a.png", "docs/b.png", "c.svg")


def test_readme_name_is_case_insensitive(tmp_path: Path) -> None:
    (tmp_path / "readme.rst").write_text('.. image:: x.png\n<img src="y.png">\n')
    assert readme_images(tmp_path) == ("y.png",)


def test_readme_images_missing_readme(tmp_path: Path) -> None:
    assert readme_images(tmp_path) == ()
    assert readme_images(tmp_path / "gone") == ()


def test_readme_images_reads_a_bounded_prefix(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("x" * 300_000 + "![a](late.png)\n")
    assert readme_images(tmp_path) == ()
