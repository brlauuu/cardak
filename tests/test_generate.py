import shutil
import subprocess
from pathlib import Path

import pytest

from cardak.core.config import Config, Project
from cardak.core.themes import Scheme
from cardak.wezterm.generate import (
    MODULE_VERSION,
    GenerateError,
    install_tint_hook,
    lua_str,
    module_version,
    render_projects_lua,
    render_tints,
    write_generated,
)
from cardak.wezterm.install import MARKER, install, module_text

ANSI = ("#000000", "#bb0000", "#00bb00", "#bbbb00", "#0000bb", "#bb00bb", "#00bbbb", "#bbbbbb")
NORD = Scheme("Nord", "#2e3440", "#d8dee9", "#eceff4", ANSI)
SCHEMES = {"Nord": NORD}
CONFIG = Config(
    projects=(
        Project(name="web", path=Path("/r/web"), theme="Nord"),
        Project(name="api", path=Path("/r/api")),
        Project(name="typo", path=Path("/r/typo"), theme="Nrod"),
    ),
    defaults={"theme": "Nord"},
)


def test_lua_strings_survive_closing_brackets() -> None:
    assert lua_str("/r/web") == "[[/r/web]]"
    assert lua_str("x]]y") == "[=[x]]y]=]"
    assert lua_str("a]=]b]]") == "[==[a]=]b]]]==]"
    assert lua_str("it's \\ fine") == "[[it's \\ fine]]"


def test_render_projects_lua() -> None:
    text, problems = render_projects_lua(CONFIG, set(SCHEMES))
    assert text == (
        f"{MARKER} by cardak from projects.toml; edits are overwritten.\n"
        "return {\n"
        "  default = [[Nord]],\n"
        "  projects = {\n"
        "    { path = [[/r/web]], scheme = [[Nord]] },\n"
        "  },\n"
        "}\n"
    )
    assert problems == ['unknown scheme "Nrod" for typo']


def test_render_without_known_names_trusts_config() -> None:
    text, problems = render_projects_lua(CONFIG, None)
    assert "[[Nrod]]" in text and problems == []


def test_render_tints() -> None:
    assert render_tints(CONFIG, SCHEMES).splitlines() == [
        "# cardak: generated from projects.toml; edits are overwritten.",
        "/r/web\t#3c2f4c",
    ]


def test_module_version(tmp_path: Path) -> None:
    lua = tmp_path / "cardak.lua"
    assert module_version(lua) is None
    lua.write_text(MARKER + " by `cardak wezterm install`\nlocal wezterm\n")
    assert module_version(lua) == 1
    install(tmp_path)
    assert module_version(lua) == MODULE_VERSION == 3
    lua.write_text("-- mine\n")
    assert module_version(lua) is None
    assert module_text().splitlines()[1] == "-- cardak-module: 3"


def test_write_generated_needs_the_module(tmp_path: Path) -> None:
    wez, card = tmp_path / "wezterm", tmp_path / "cardak"
    result = write_generated(wez, card, CONFIG, SCHEMES)
    assert result.module_version is None
    assert result.written == (card / "tints",)
    assert not (wez / "cardak_projects.lua").exists()


def test_write_generated_writes_only_changes(tmp_path: Path) -> None:
    wez, card = tmp_path / "wezterm", tmp_path / "cardak"
    install(wez)
    first = write_generated(wez, card, CONFIG, SCHEMES)
    assert first.written == (wez / "cardak_projects.lua", card / "tints")
    assert first.problems == ('unknown scheme "Nrod" for typo',)
    assert first.module_version == MODULE_VERSION
    assert write_generated(wez, card, CONFIG, SCHEMES).written == ()


def test_write_generated_refuses_foreign_files(tmp_path: Path) -> None:
    wez, card = tmp_path / "wezterm", tmp_path / "cardak"
    install(wez)
    (wez / "cardak_projects.lua").write_text("-- mine\nreturn {}\n")
    result = write_generated(wez, card, CONFIG, None)
    assert (wez / "cardak_projects.lua").read_text() == "-- mine\nreturn {}\n"
    assert any("was not written by cardak" in p for p in result.problems)


def test_install_tint_hook(tmp_path: Path) -> None:
    hook = install_tint_hook(tmp_path)
    assert hook == tmp_path / "tint.zsh"
    assert hook.read_text().startswith("# cardak: generated")
    (tmp_path / "tint.zsh").write_text("# mine\n")
    with pytest.raises(GenerateError, match="--force"):
        install_tint_hook(tmp_path)
    install_tint_hook(tmp_path, force=True)


@pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh not installed")
def test_tint_hook_emits_osc11(tmp_path: Path) -> None:
    card = tmp_path / "cardak"
    proj = tmp_path / "my proj"
    (proj / "sub").mkdir(parents=True)
    hook = install_tint_hook(card)
    (card / "tints").write_text(f"# cardak: generated\n{proj}\t#102030\n")
    env = {
        "HOME": str(tmp_path),
        "XDG_CONFIG_HOME": str(tmp_path),
        "TERM_PROGRAM": "WezTerm",
        "PATH": "/usr/bin:/bin",
    }
    script = f"source {hook}; cd {str(proj / 'sub')!r}; cd /"
    out = subprocess.run(["zsh", "-f", "-c", script], env=env, capture_output=True, check=True)
    assert out.stdout == b"\x1b]111\x07\x1b]11;#102030\x07\x1b]111\x07"
    env["TERM_PROGRAM"] = "Konsole"
    quiet = subprocess.run(["zsh", "-f", "-c", script], env=env, capture_output=True, check=True)
    assert quiet.stdout == b""


def test_lua_strings_ending_in_a_bracket() -> None:
    assert lua_str("a]") == "[=[a]]=]"
    assert lua_str("a]=") == "[[a]=]]"  # the first "]]" comes after the whole value


@pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh not installed")
def test_tint_hook_stays_out_of_command_substitution(tmp_path: Path) -> None:
    card = tmp_path / "cardak"
    proj = tmp_path / "proj"
    proj.mkdir()
    hook = install_tint_hook(card)
    (card / "tints").write_text(f"# cardak: generated\n{proj}\t#102030\n")
    env = {
        "HOME": str(tmp_path),
        "XDG_CONFIG_HOME": str(tmp_path),
        "TERM_PROGRAM": "WezTerm",
        "PATH": "/usr/bin:/bin",
    }
    script = f'source {hook} >/dev/null; x=$(cd {str(proj)!r}; pwd); print -r -- "X=$x"'
    out = subprocess.run(["zsh", "-f", "-c", script], env=env, capture_output=True, check=True)
    assert out.stdout == f"X={proj}\n".encode()


DUPES = Config(
    projects=(
        Project(name="first", path=Path("/r/web"), theme="Nord"),
        Project(name="second", path=Path("/r/web"), theme="Dracula"),
    )
)


def test_duplicate_paths_first_wins_and_is_reported() -> None:
    text, problems = render_projects_lua(DUPES, None)
    assert "[[Nord]]" in text and "Dracula" not in text  # the deck's resolver uses the first too
    assert problems == ["duplicate path /r/web: second is ignored, first is used"]
    assert render_tints(DUPES, SCHEMES).splitlines()[1:] == ["/r/web\t#3c2f4c"]


def test_duplicate_paths_first_without_theme_wins() -> None:
    config = Config(
        projects=(
            Project(name="plain", path=Path("/r/web")),
            Project(name="themed", path=Path("/r/web"), theme="Nord"),
        )
    )
    text, _ = render_projects_lua(config, None)
    assert "Nord" not in text and render_tints(config, SCHEMES).splitlines()[1:] == []


def test_tints_skip_paths_with_tab_or_newline() -> None:
    config = Config(
        projects=(
            Project(name="tab", path=Path("/r/a\tb"), theme="Nord"),
            Project(name="nl", path=Path("/r/a\nb"), theme="Nord"),
            Project(name="web", path=Path("/r/web"), theme="Nord"),
        )
    )
    assert render_tints(config, SCHEMES).splitlines()[1:] == ["/r/web\t#3c2f4c"]


def test_write_generated_reports_tint_problems(tmp_path: Path) -> None:
    config = Config(projects=(Project(name="tab", path=Path("/r/a\tb"), theme="Nord"),))
    result = write_generated(tmp_path / "wezterm", tmp_path / "cardak", config, SCHEMES)
    assert result.problems == ("tab: its path can't go in the tints file (tab or newline)",)


def test_symlinked_project_path_is_themed_both_ways(tmp_path: Path) -> None:
    real = tmp_path / "disk" / "web"
    real.mkdir(parents=True)
    link = tmp_path / "web"
    link.symlink_to(real)
    text, problems = render_projects_lua(
        Config(projects=(Project(name="web", path=link, theme="Nord"),)), None
    )
    assert f"path = {lua_str(str(link))}" in text and f"path = {lua_str(str(real))}" in text
    assert problems == []


def test_symlinked_project_path_is_tinted_both_ways(tmp_path: Path) -> None:
    real = tmp_path / "disk" / "web"
    real.mkdir(parents=True)
    link = tmp_path / "web"
    link.symlink_to(real)
    tints = render_tints(Config(projects=(Project(name="web", path=link, theme="Nord"),)), SCHEMES)
    assert tints.splitlines()[1:] == [f"{link}\t#3c2f4c", f"{real}\t#3c2f4c"]


def test_two_projects_with_the_same_real_directory_are_duplicates(tmp_path: Path) -> None:
    real = tmp_path / "disk" / "web"
    real.mkdir(parents=True)
    link = tmp_path / "web"
    link.symlink_to(real)
    config = Config(
        projects=(
            Project(name="A", path=link, theme="Nord"),
            Project(name="B", path=real, theme="Dracula"),
        )
    )
    text, problems = render_projects_lua(config, None)
    assert "Dracula" not in text  # one scheme per directory: WezTerm can't pick at random
    assert problems == [f"duplicate path {real}: B is ignored, A is used"]
