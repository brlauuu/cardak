from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_state(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Keep every test away from the real ~/.local/state/cardak (the agent database), the
    real ~/.config (the Anthropic key a ping would hand to a spawned `cardak summarize`) and
    the real ~/.cache (the downloaded prices)."""
    state = tmp_path_factory.mktemp("state")
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path_factory.mktemp("config")))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path_factory.mktemp("cache")))
    return state


@pytest.fixture(autouse=True)
def no_transcripts_or_downloads(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deck or daemon started by a test counts an empty transcripts folder, and downloads
    nothing: the real ~/.claude/projects and the network stay out of every test (#29)."""
    from cardak.collectors import prices, usage

    root = tmp_path_factory.mktemp("transcripts")
    monkeypatch.setattr(usage, "default_root", lambda: root)

    def offline(*_args: object, **_kwargs: object) -> bytes:
        raise OSError("tests never download")

    monkeypatch.setattr(prices, "fetch", offline)


@pytest.fixture(autouse=True)
def packs_dir(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Sound packs come from an empty folder of the test's own, never the user's packs in
    src/cardak/sounds/packs (#91)."""
    from cardak.core import alerts

    root = tmp_path_factory.mktemp("packs")
    monkeypatch.setattr(alerts, "pack_root", lambda: root)
    return root
