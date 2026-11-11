from importlib import metadata

import pytest

from cardak.shared import meta


def test_version_is_the_installed_one() -> None:
    assert meta.version() == metadata.version("cardak")


def test_an_uninstalled_tree_is_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    def gone(name: str) -> str:
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(metadata, "version", gone)
    meta.version.cache_clear()
    try:
        assert meta.version() == "dev"
    finally:
        meta.version.cache_clear()
