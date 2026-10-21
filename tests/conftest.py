from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_state(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Keep every test away from the real ~/.local/state/cardak (the agent database)."""
    state = tmp_path_factory.mktemp("state")
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    return state
