import os
import stat
from pathlib import Path

import pytest

from cardak.shared.files import write_atomic


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_new_file_gets_new_mode_and_parents(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b" / "f.txt"
    write_atomic(target, "hi\n")
    assert target.read_text() == "hi\n" and mode(target) == 0o644
    write_atomic(tmp_path / "secret", "k\n", new_mode=0o600)
    assert mode(tmp_path / "secret") == 0o600


def test_existing_mode_is_kept_unless_forced(tmp_path: Path) -> None:
    target = tmp_path / "f"
    target.write_text("old")
    target.chmod(0o640)
    write_atomic(target, "new")
    assert target.read_text() == "new" and mode(target) == 0o640
    write_atomic(target, "newer", mode=0o600)  # a key: owner only, whatever it was
    assert mode(target) == 0o600


def test_bytes_are_written_as_is(tmp_path: Path) -> None:
    write_atomic(tmp_path / "img", b"\x89PNG\x00")
    assert (tmp_path / "img").read_bytes() == b"\x89PNG\x00"


def test_writes_through_a_symlink(tmp_path: Path) -> None:
    # dotfiles kept in a repo and linked into place: the target changes, the link stays
    real = tmp_path / "dotfiles" / "f"
    real.parent.mkdir()
    real.write_text("old")
    real.chmod(0o600)
    link = tmp_path / "f"
    link.symlink_to(real)
    write_atomic(link, "new")
    assert link.is_symlink() and real.read_text() == "new" and mode(real) == 0o600


def test_a_dangling_symlink_creates_its_target(tmp_path: Path) -> None:
    link = tmp_path / "f"
    link.symlink_to(tmp_path / "elsewhere" / "f")
    write_atomic(link, "x")
    assert link.is_symlink() and (tmp_path / "elsewhere" / "f").read_text() == "x"


def test_a_failed_write_leaves_no_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "f"
    target.write_text("old")

    def broken(src: object, dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", broken)
    with pytest.raises(OSError, match="disk full"):
        write_atomic(target, "new")
    assert [p.name for p in tmp_path.iterdir()] == ["f"] and target.read_text() == "old"
