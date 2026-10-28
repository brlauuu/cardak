"""Tests never see the user's real config (their Anthropic key, projects.toml, cardak.lua),
state or cache."""

from pathlib import Path

from cardak.core.config import anthropic_key_path, cache_dir, config_dir, state_dir


def test_config_and_state_dirs_are_temporary() -> None:
    home = Path.home()
    for path in (config_dir(), anthropic_key_path(), state_dir()):
        assert not path.is_relative_to(home / ".config"), path
        assert not path.is_relative_to(home / ".local" / "state"), path


def test_cache_dir_is_temporary() -> None:
    assert not cache_dir().is_relative_to(Path.home() / ".cache")


def test_transcripts_and_price_download_are_fakes() -> None:
    import pytest

    from cardak.collectors import prices, usage

    assert not usage.default_root().is_relative_to(Path.home() / ".claude")
    with pytest.raises(OSError):
        prices.fetch()
