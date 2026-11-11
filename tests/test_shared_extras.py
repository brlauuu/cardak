import importlib.util

import pytest

from cardak.shared import extras


def test_extras_name_their_modules() -> None:
    assert extras.EXTRAS == {
        "background": ("PIL", "pyfiglet", "resvg_py"),
        "anthropic": ("anthropic",),
    }


def test_missing_asks_the_import_system_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(extras, "EXTRAS", {"x": ("json", "no_such_module_131")}, raising=True)
    monkeypatch.setattr(extras, "_found", {})
    asked: list[str] = []
    real = importlib.util.find_spec

    def spy(name: str) -> object:
        asked.append(name)
        return real(name)

    monkeypatch.setattr(importlib.util, "find_spec", spy)
    assert extras.missing("x") and extras.missing("x")
    assert asked == ["json", "no_such_module_131"]


def test_an_installed_extra_is_not_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(extras, "EXTRAS", {"x": ("json",)})
    monkeypatch.setattr(extras, "_found", {})
    assert not extras.missing("x")


def test_needs_names_the_extra_and_the_command() -> None:
    text = extras.needs("background", "the project background")
    assert text == (
        "the project background needs cardak's background extra: uv tool install"
        " --reinstall 'cardak[all] @ git+https://github.com/brlauuu/cardak'"
    )


def test_the_block_fixture_makes_an_extra_missing(block_extra: object) -> None:
    block_extra("anthropic")  # type: ignore[operator]
    assert extras.missing("anthropic")
