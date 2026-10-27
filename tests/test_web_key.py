from pathlib import Path

import pytest

from cardak.web.key import WebKeyError, ensure_key, read_key


def test_ensure_key_creates_0600_and_keeps(tmp_path: Path) -> None:
    path = tmp_path / "cardak" / "web_key"
    key, created = ensure_key(path)
    assert created
    assert len(key) >= 43
    assert path.stat().st_mode & 0o777 == 0o600
    assert read_key(path) == key
    assert ensure_key(path) == (key, False)
    new, created = ensure_key(path, new=True)
    assert created and new != key
    assert read_key(path) == new
    assert path.stat().st_mode & 0o777 == 0o600


def test_read_key_refuses_group_bits(tmp_path: Path) -> None:
    path = tmp_path / "web_key"
    ensure_key(path)
    path.chmod(0o640)
    with pytest.raises(WebKeyError, match="readable"):
        read_key(path)


def test_read_key_missing_or_empty(tmp_path: Path) -> None:
    with pytest.raises(WebKeyError):
        read_key(tmp_path / "nope")
    empty = tmp_path / "empty"
    empty.write_text("\n")
    empty.chmod(0o600)
    with pytest.raises(WebKeyError):
        read_key(empty)
