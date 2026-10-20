import os
from pathlib import Path

import pytest

from cardak.wezterm.install import (
    MARKER,
    InstallError,
    default_dir,
    install,
    module_text,
    snippet,
)


def test_packaged_module_is_readable_and_registers_the_handler() -> None:
    text = module_text()
    assert text.startswith(MARKER)
    assert "user-var-changed" in text
    assert "cardak_focus" in text
    assert "function M.apply(config)" in text


def test_fresh_install(tmp_path: Path) -> None:
    result = install(tmp_path / "wezterm")
    assert result.status == "installed"
    assert result.path == tmp_path / "wezterm" / "cardak.lua"
    assert result.path.read_text() == module_text()
    assert result.path.stat().st_mode & 0o777 == 0o644


def test_reinstall_is_unchanged(tmp_path: Path) -> None:
    install(tmp_path)
    assert install(tmp_path).status == "unchanged"


def test_older_cardak_file_is_updated(tmp_path: Path) -> None:
    (tmp_path / "cardak.lua").write_text(MARKER + " (old)\nreturn {}\n")
    assert install(tmp_path).status == "updated"
    assert (tmp_path / "cardak.lua").read_text() == module_text()


def test_install_refuses_foreign_file(tmp_path: Path) -> None:
    mine = "-- my own cardak.lua\nreturn {}\n"
    (tmp_path / "cardak.lua").write_text(mine)
    with pytest.raises(InstallError, match="--force"):
        install(tmp_path)
    assert (tmp_path / "cardak.lua").read_text() == mine
    assert [p.name for p in tmp_path.iterdir()] == ["cardak.lua"]


def test_force_replaces_foreign_file(tmp_path: Path) -> None:
    (tmp_path / "cardak.lua").write_text("-- mine\n")
    assert install(tmp_path, force=True).status == "updated"
    assert (tmp_path / "cardak.lua").read_text() == module_text()


def test_unwritable_dir_is_an_install_error(tmp_path: Path) -> None:
    d = tmp_path / "ro"
    d.mkdir()
    d.chmod(0o500)
    try:
        with pytest.raises(InstallError, match="cannot write"):
            install(d)
    finally:
        d.chmod(0o700)


def test_default_dir(tmp_path: Path) -> None:
    assert default_dir({"XDG_CONFIG_HOME": "/xdg"}) == Path("/xdg/wezterm")
    assert default_dir({"HOME": str(tmp_path)}) == tmp_path / ".config/wezterm"


def test_snippet_for_default_dir(tmp_path: Path) -> None:
    env = {"HOME": str(tmp_path)}
    text = snippet(default_dir(env), env)
    assert "require('cardak').apply(config)" in text
    assert "return config" in text
    assert "package.path" not in text


def test_snippet_for_other_dir_adds_package_path(tmp_path: Path) -> None:
    text = snippet(tmp_path / "elsewhere", {"HOME": str(tmp_path)})
    assert f"package.path = [==[{tmp_path / 'elsewhere'}/?.lua;]==] .. package.path" in text


def test_snippet_quotes_odd_paths_safely(tmp_path: Path) -> None:
    odd = tmp_path / "it's \\ here"
    text = snippet(odd, {"HOME": str(tmp_path)})
    assert f"[==[{odd}/?.lua;]==]" in text


def test_handler_body_is_protected() -> None:
    # a pane or tab closing mid-event must not raise a WezTerm error overlay
    text = module_text()
    assert "pcall(focus_target, id)" in text
    assert "local win = tab:window()" in text


def test_install_writes_through_a_symlink(tmp_path: Path) -> None:
    dotfiles = tmp_path / "dotfiles"
    dotfiles.mkdir()
    target = dotfiles / "cardak.lua"
    target.write_text(MARKER + " (old)\n")
    (tmp_path / "wez").mkdir()
    link = tmp_path / "wez" / "cardak.lua"
    link.symlink_to(target)
    assert install(tmp_path / "wez").status == "updated"
    assert link.is_symlink()
    assert target.read_text() == module_text()


def test_install_does_not_touch_wezterm_lua(tmp_path: Path) -> None:
    config = tmp_path / "wezterm.lua"
    config.write_text("return {}\n")
    before = os.stat(config)
    install(tmp_path)
    assert config.read_text() == "return {}\n"
    assert os.stat(config).st_mtime_ns == before.st_mtime_ns
