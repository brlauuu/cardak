"""The project background with its extra not installed (#131): runs in the base CI job too."""

from collections.abc import Callable
from pathlib import Path

import pytest

from cardak.core.backgrounds import Paths, Tools, ensure_art, images
from cardak.core.config import Config, Project
from cardak.shared import extras


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    return Paths(tmp_path / "logos", tmp_path / "state" / "backgrounds.json", tmp_path / "cache")


def no_logo(_: Path) -> list[Path]:
    return []


def project(tmp_path: Path, **kw: object) -> Project:
    path = tmp_path / "repos" / "cardak"
    path.mkdir(parents=True, exist_ok=True)
    return Project("cardak", path, **kw)  # type: ignore[arg-type]


def config(*projects: Project) -> Config:
    return Config(projects=projects)


def test_tools_say_the_background_extra_is_missing(
    tmp_path: Path, paths: Paths, block_extra: Callable[[str], None]
) -> None:
    block_extra("background")
    off = Tools(paths, lambda: config(project(tmp_path)), lambda: {}, lambda: None).off_reason()
    assert off == extras.needs("background", "the project background")


def test_without_the_background_extra_no_art_is_made(
    tmp_path: Path, paths: Paths, block_extra: Callable[[str], None]
) -> None:
    block_extra("background")
    cfg = config(project(tmp_path))
    hint = [extras.needs("background", "the project background")]
    assert ensure_art(cfg, paths, no_logo) == hint
    assert images(cfg, {}, paths, None) == ({}, hint)
    assert not paths.art_dir.exists() and not paths.record.exists()


def test_without_the_extra_backgrounds_off_say_nothing(
    tmp_path: Path, paths: Paths, block_extra: Callable[[str], None]
) -> None:
    block_extra("background")
    cfg = config(project(tmp_path, background=False))
    assert ensure_art(cfg, paths, no_logo) == [] and images(cfg, {}, paths, None) == ({}, [])
