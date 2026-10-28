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
