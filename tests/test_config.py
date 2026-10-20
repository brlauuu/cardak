from pathlib import Path

import pytest

from cardak.config import Config, ConfigError, Project, add_project, config_path, load

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
