from pathlib import Path

import pytest

from cardak.core.config import Config, ConfigError, Project, add_project, config_path, load

SAMPLE = """\
# my projects
[defaults]
theme = "Tokyo Night"

[[project]]
name  = "webshop"
path  = "~/repos/webshop"
theme = "Catppuccin Mocha"
accent = "#cba6f7"
github = "example/webshop"

[[project]]
name = "api-gw"
path = "/srv/api-gw"
"""


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def test_missing_file_is_empty_config(tmp_path: Path) -> None:
    assert load(tmp_path / "nope.toml") == Config()


def test_load_sample(tmp_path: Path, home: Path) -> None:
    f = tmp_path / "projects.toml"
    f.write_text(SAMPLE)
    cfg = load(f)
    assert cfg.projects == (
        Project(
            name="webshop",
            path=home / "repos/webshop",
            theme="Catppuccin Mocha",
            accent="#cba6f7",
            github="example/webshop",
        ),
        Project(name="api-gw", path=Path("/srv/api-gw")),
    )
    assert cfg.defaults == {"theme": "Tokyo Night"}


def test_invalid_toml_reports_line(tmp_path: Path) -> None:
    f = tmp_path / "projects.toml"
    f.write_text('[[project]]\nname = "x"\npath = \n')
    with pytest.raises(ConfigError) as exc:
        load(f)
    assert exc.value.line == 3
    assert str(exc.value).startswith("projects.toml line 3: ")


def test_project_without_path_is_an_error(tmp_path: Path) -> None:
    f = tmp_path / "projects.toml"
    f.write_text('[[project]]\nname = "x"\n')
    with pytest.raises(ConfigError, match="project #1 needs a string 'name' and 'path'"):
        load(f)


def test_project_must_be_array_of_tables(tmp_path: Path) -> None:
    f = tmp_path / "projects.toml"
    f.write_text('project = "x"\n')
    with pytest.raises(ConfigError, match=r"\[\[project\]\]"):
        load(f)


def test_defaults_must_be_table(tmp_path: Path) -> None:
    f = tmp_path / "projects.toml"
    f.write_text('defaults = "x"\n')
    with pytest.raises(ConfigError, match=r"\[defaults\]"):
        load(f)


def test_add_project_creates_file_and_dirs(tmp_path: Path, home: Path) -> None:
    f = tmp_path / "cfg" / "cardak" / "projects.toml"
    add_project(f, "cardak", home / "repos" / "cardak")
    assert f.read_text() == '[[project]]\nname = "cardak"\npath = "~/repos/cardak"\n'
    assert load(f).projects == (Project(name="cardak", path=home / "repos/cardak"),)


def test_add_project_keeps_comments_and_layout(tmp_path: Path, home: Path) -> None:
    f = tmp_path / "projects.toml"
    f.write_text(SAMPLE)
    add_project(f, "notes", Path("/opt/notes"))
    text = f.read_text()
    assert text.startswith(SAMPLE)
    assert text.endswith('\n[[project]]\nname = "notes"\npath = "/opt/notes"\n')
    assert [p.name for p in load(f).projects] == ["webshop", "api-gw", "notes"]


def test_add_project_refuses_duplicate(tmp_path: Path, home: Path) -> None:
    f = tmp_path / "projects.toml"
    f.write_text(SAMPLE)
    with pytest.raises(ConfigError, match="already configured"):
        add_project(f, "again", home / "repos" / "webshop")


def test_add_project_refuses_broken_file_and_leaves_it(tmp_path: Path) -> None:
    f = tmp_path / "projects.toml"
    f.write_text("this is = = not toml\n")
    with pytest.raises(ConfigError):
        add_project(f, "x", Path("/x"))
    assert f.read_text() == "this is = = not toml\n"
    assert [p.name for p in tmp_path.iterdir()] == ["projects.toml"]


def test_config_path_precedence(tmp_path: Path) -> None:
    assert config_path("/a/b.toml", env={"CARDAK_CONFIG": "/c.toml"}) == Path("/a/b.toml")
    assert config_path(None, env={"CARDAK_CONFIG": "/c.toml"}) == Path("/c.toml")
    assert config_path(None, env={"XDG_CONFIG_HOME": "/xdg"}) == Path("/xdg/cardak/projects.toml")
    assert (
        config_path(None, env={"HOME": str(tmp_path)}) == tmp_path / ".config/cardak/projects.toml"
    )


def test_unreadable_or_undecodable_file_is_a_config_error(tmp_path: Path) -> None:
    f = tmp_path / "projects.toml"
    f.write_bytes(b"\xff\xfe not utf-8")
    with pytest.raises(ConfigError):
        load(f)
    with pytest.raises(ConfigError):
        load(tmp_path)  # a directory


def test_add_project_write_failure_is_a_config_error(tmp_path: Path) -> None:
    d = tmp_path / "ro"
    d.mkdir()
    d.chmod(0o500)
    try:
        with pytest.raises(ConfigError):
            add_project(d / "projects.toml", "x", Path("/x"))
    finally:
        d.chmod(0o700)


def test_add_project_refuses_when_result_would_not_parse(tmp_path: Path) -> None:
    f = tmp_path / "projects.toml"
    inline = 'project = [{ name = "a", path = "/a" }]\n'
    f.write_text(inline)
    assert [p.name for p in load(f).projects] == ["a"]
    with pytest.raises(ConfigError, match="add it by hand"):
        add_project(f, "b", Path("/b"))
    assert f.read_text() == inline


def test_add_project_keeps_symlink_and_mode(tmp_path: Path) -> None:
    dotfiles = tmp_path / "dotfiles"
    dotfiles.mkdir()
    target = dotfiles / "projects.toml"
    target.write_text(SAMPLE)
    target.chmod(0o644)
    link = tmp_path / "projects.toml"
    link.symlink_to(target)
    add_project(link, "notes", Path("/opt/notes"))
    assert link.is_symlink()
    assert target.read_text().endswith('path = "/opt/notes"\n')
    assert target.stat().st_mode & 0o777 == 0o644


def test_new_config_file_is_world_readable(tmp_path: Path) -> None:
    f = tmp_path / "projects.toml"
    add_project(f, "x", Path("/x"))
    assert f.stat().st_mode & 0o777 == 0o644
