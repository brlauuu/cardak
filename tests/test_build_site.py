import importlib.util
import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent

_spec = importlib.util.spec_from_file_location("build_site", ROOT / "scripts" / "build_site.py")
assert _spec and _spec.loader
build_site = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_site)


def _site(tmp_path: Path, page: str) -> tuple[Path, Path]:
    src = tmp_path / "site"
    (src / "assets").mkdir(parents=True)
    (src / "index.html").write_text(page)
    (src / "assets" / "x.svg").write_text("<svg/>")
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "cardak"\nversion = "1.2.3"\n')
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## Unreleased\n\n## 1.2.3 — 2026-01-02\n"
    )
    return src, pyproject


def _build(src: Path, out: Path, pyproject: Path) -> str:
    return build_site.build(src, out, pyproject, pyproject.parent / "CHANGELOG.md")


def test_build_fills_the_version(tmp_path: Path) -> None:
    src, pyproject = _site(tmp_path, "<p>v__VERSION__</p>")
    out = tmp_path / "out"
    assert _build(src, out, pyproject) == "1.2.3"
    assert (out / "index.html").read_text() == "<p>v1.2.3</p>"
    assert (out / "assets" / "x.svg").read_text() == "<svg/>"


def test_build_fills_the_release_date(tmp_path: Path) -> None:
    # the banner shows for 30 days after the release; the page compares, the build dates it (#164)
    src, pyproject = _site(tmp_path, '<a data-released="__RELEASED__">v__VERSION__</a>')
    out = tmp_path / "out"
    _build(src, out, pyproject)
    assert (out / "index.html").read_text() == '<a data-released="2026-01-02">v1.2.3</a>'


def test_no_dated_heading_means_no_date(tmp_path: Path) -> None:
    # a version bumped but not yet cut has no dated heading: no banner, and the build passes
    assert build_site.released("## Unreleased\n\n## 1.2.2 — 2026-01-01\n", "1.2.3") == ""
    assert build_site.released("## 1.2.3 - 2026-01-05\n", "1.2.3") == "2026-01-05"
    assert build_site.released("## 1.2.30 — 2026-01-05\n", "1.2.3") == ""


def test_build_replaces_an_old_output(tmp_path: Path) -> None:
    src, pyproject = _site(tmp_path, "<p>v__VERSION__</p>")
    out = tmp_path / "out"
    out.mkdir()
    (out / "old.txt").write_text("stale")
    _build(src, out, pyproject)
    assert not (out / "old.txt").exists()


def test_leftover_placeholder_fails(tmp_path: Path) -> None:
    src, pyproject = _site(tmp_path, "<p>v__VERSION__ · __STARS__ stars</p>")
    with pytest.raises(ValueError, match="__STARS__"):
        _build(src, tmp_path / "out", pyproject)


def test_real_site_builds(tmp_path: Path) -> None:
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    out = tmp_path / "out"
    assert (
        build_site.build(ROOT / "site", out, ROOT / "pyproject.toml", ROOT / "CHANGELOG.md")
        == version
    )
    page = (out / "index.html").read_text()
    assert f"v{version}" in page
    assert not re.search(r"__[A-Z_]{2,}__", page)


def test_main_reports_a_leftover_placeholder(tmp_path: Path, monkeypatch, capsys) -> None:
    src, pyproject = _site(tmp_path, "__STARS__")
    monkeypatch.setattr(build_site, "SITE", src)
    monkeypatch.setattr(build_site, "OUT", tmp_path / "out")
    monkeypatch.setattr(build_site, "PYPROJECT", pyproject)
    monkeypatch.setattr(build_site, "CHANGELOG", tmp_path / "CHANGELOG.md")
    assert build_site.main() == 1
    assert "__STARS__" in capsys.readouterr().err


def test_workflow_triggers_and_permissions() -> None:
    text = (ROOT / ".github" / "workflows" / "pages.yml").read_text()
    assert "branches: [main]" in text
    inputs = (
        "site/**",
        "pyproject.toml",
        "CHANGELOG.md",  # the release date (#164)
        ".github/workflows/pages.yml",
        "scripts/build_site.py",
    )
    for path in inputs:
        assert f'"{path}"' in text, path
    assert "workflow_dispatch:" in text
    # the github-pages environment takes deployments from the default branch only
    assert "tags:" not in text
    for line in ("contents: read", "pages: write", "id-token: write"):
        assert line in text
    assert "scripts/build_site.py" in text
    assert "actions/upload-pages-artifact@v5" in text
    assert "path: _site" in text
    assert "actions/deploy-pages@v5" in text


def test_setup_uv_is_pinned_to_an_exact_version() -> None:
    # since v8, setup-uv publishes no major or minor tags: `@v10` doesn't resolve (#106)
    text = (ROOT / ".github" / "workflows" / "test.yml").read_text()
    pins = re.findall(r"astral-sh/setup-uv@(\S+)", text)
    assert pins and all(re.fullmatch(r"v\d+\.\d+\.\d+", p) for p in pins), pins
