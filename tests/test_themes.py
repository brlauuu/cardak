from pathlib import Path

import pytest

from cardak.core.config import Project
from cardak.core.themes import (
    Scheme,
    SchemeStore,
    contrast,
    default_accent,
    project_accent,
    project_tint,
    tint,
)

ANSI = ("#000000", "#bb0000", "#00bb00", "#bbbb00", "#0000bb", "#bb00bb", "#00bbbb", "#bbbbbb")
BUILTIN_DARK = Scheme("Builtin Dark", "#000000", "#bbbbbb", "#bbbbbb", ANSI)
REDCURSOR = Scheme("Red", "#000000", "#ffffff", "#ff0000", ANSI)
SCHEMES = {s.name: s for s in (BUILTIN_DARK, REDCURSOR)}


def test_contrast() -> None:
    assert contrast("#000000", "#ffffff") == pytest.approx(21.0)
    assert contrast("#123456", "#123456") == pytest.approx(1.0)


def test_default_accent_prefers_a_distinct_cursor() -> None:
    assert default_accent(REDCURSOR) == "#ff0000"


def test_default_accent_falls_back_to_magenta() -> None:
    assert default_accent(BUILTIN_DARK) == "#bb00bb"  # cursor_bg == foreground
    assert default_accent(Scheme("x", "#000000", "#ffffff", None, ANSI)) == "#bb00bb"


def test_tint_mixes_ten_percent() -> None:
    assert tint("#102030", "#f0e0d0") == "#263340"
    assert tint("#000000", "#ffffff", 0.0) == "#000000"


def test_project_accent() -> None:
    p = Project(name="a", path=Path("/a"))
    assert project_accent(p, SCHEMES) is None
    assert project_accent(Project(name="a", path=Path("/a"), theme="Red"), SCHEMES) == "#ff0000"
    explicit = Project(name="a", path=Path("/a"), theme="Red", accent="#00ff00")
    assert project_accent(explicit, SCHEMES) == "#00ff00"
    assert project_accent(Project(name="a", path=Path("/a"), theme="Unknown"), SCHEMES) is None


def test_project_tint() -> None:
    assert project_tint(Project(name="a", path=Path("/a")), SCHEMES) is None
    assert project_tint(Project(name="a", path=Path("/a"), tint="#010203"), SCHEMES) == "#010203"
    themed = Project(name="a", path=Path("/a"), theme="Red")
    assert project_tint(themed, SCHEMES) == tint("#000000", "#ff0000")


def test_scheme_store() -> None:
    store = SchemeStore()
    assert store.get() == {}
    store.set(SCHEMES)
    assert store.get()["Red"] is REDCURSOR
