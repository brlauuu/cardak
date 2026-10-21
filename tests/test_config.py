from pathlib import Path

import pytest

from cardak.core.config import (
    Config,
    ConfigError,
    NotifySettings,
    Project,
    SummarySettings,
    add_project,
    anthropic_key_path,
    config_dir,
    config_path,
    load,
    set_project_theme,
)

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


def test_tint_is_loaded(tmp_path: Path) -> None:
    f = tmp_path / "projects.toml"
    f.write_text('[[project]]\nname = "a"\npath = "/a"\ntint = "#102030"\n')
    assert load(f).projects[0].tint == "#102030"


def test_config_dir(tmp_path: Path) -> None:
    assert config_dir({"XDG_CONFIG_HOME": "/xdg"}) == Path("/xdg/cardak")
    assert config_dir({"HOME": str(tmp_path)}) == tmp_path / ".config/cardak"


def test_add_project_with_theme(tmp_path: Path, home: Path) -> None:
    f = tmp_path / "projects.toml"
    add_project(f, "notes", Path("/opt/notes"), theme="Nord")
    assert f.read_text() == '[[project]]\nname = "notes"\npath = "/opt/notes"\ntheme = "Nord"\n'


def test_set_project_theme_keeps_comments_and_layout(tmp_path: Path, home: Path) -> None:
    f = tmp_path / "projects.toml"
    f.write_text(SAMPLE.replace('theme = "Catppuccin Mocha"', 'theme = "Catppuccin Mocha"  # mine'))
    set_project_theme(f, home / "repos" / "webshop", "Tokyo Night")
    assert f.read_text() == SAMPLE.replace(
        'theme = "Catppuccin Mocha"', 'theme = "Tokyo Night"  # mine'
    )


def test_set_project_theme_inserts_when_absent(tmp_path: Path, home: Path) -> None:
    f = tmp_path / "projects.toml"
    f.write_text(SAMPLE)
    set_project_theme(f, Path("/srv/api-gw"), "Nord")
    text = f.read_text()
    assert text.startswith("# my projects\n")
    assert [(p.name, p.theme) for p in load(f).projects] == [
        ("webshop", "Catppuccin Mocha"),
        ("api-gw", "Nord"),
    ]


def test_set_project_theme_unknown_project(tmp_path: Path, home: Path) -> None:
    f = tmp_path / "projects.toml"
    f.write_text(SAMPLE)
    with pytest.raises(ConfigError, match="not a configured project"):
        set_project_theme(f, Path("/nope"), "Nord")
    assert f.read_text() == SAMPLE


def test_set_project_theme_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not a configured project"):
        set_project_theme(tmp_path / "projects.toml", Path("/a"), "Nord")


def test_set_project_theme_refuses_inline_projects(tmp_path: Path) -> None:
    f = tmp_path / "projects.toml"
    inline = 'project = [{ name = "a", path = "/a" }]\n'
    f.write_text(inline)
    with pytest.raises(ConfigError, match="edit it by hand"):
        set_project_theme(f, Path("/a"), "Nord")
    assert f.read_text() == inline


def test_set_project_theme_refuses_broken_file(tmp_path: Path) -> None:
    f = tmp_path / "projects.toml"
    f.write_text("this is = = not toml\n")
    with pytest.raises(ConfigError):
        set_project_theme(f, Path("/a"), "Nord")
    assert f.read_text() == "this is = = not toml\n"


def test_set_project_theme_keeps_symlink_and_mode(tmp_path: Path, home: Path) -> None:
    target = tmp_path / "dotfiles.toml"
    target.write_text(SAMPLE)
    target.chmod(0o640)
    link = tmp_path / "projects.toml"
    link.symlink_to(target)
    set_project_theme(link, Path("/srv/api-gw"), "Nord")
    assert link.is_symlink()
    assert target.stat().st_mode & 0o777 == 0o640
    assert load(link).projects[1].theme == "Nord"


def test_set_project_theme_inserts_before_the_next_sections_comment(tmp_path: Path) -> None:
    f = tmp_path / "projects.toml"
    before = (
        '[[project]]\nname = "a"\npath = "/a"\n\n'
        '# --- section b ---\n[[project]]\nname = "b"\npath = "/b"\n'
    )
    f.write_text(before)
    set_project_theme(f, Path("/a"), "Nord")
    assert f.read_text() == (
        '[[project]]\nname = "a"\npath = "/a"\ntheme = "Nord"\n\n'
        '# --- section b ---\n[[project]]\nname = "b"\npath = "/b"\n'
    )


def test_notify_defaults(tmp_path: Path) -> None:
    f = tmp_path / "p.toml"
    f.write_text(SAMPLE)
    assert load(f).notify == NotifySettings(desktop=True, sound="random", sounds_dir=None)


def test_notify_parsed(tmp_path: Path, home: Path) -> None:
    f = tmp_path / "p.toml"
    f.write_text('[notify]\ndesktop = false\nsound = "bell"\nsounds_dir = "~/sounds"\n')
    assert load(f).notify == NotifySettings(False, "bell", home / "sounds")


@pytest.mark.parametrize(
    "text",
    [
        '[notify]\ndesktop = "yes"\n',
        "notify = 3\n",
        "[notify]\nsound = 1\n",
        "[notify]\nsounds_dir = []\n",
    ],
)
def test_notify_bad_types(tmp_path: Path, text: str) -> None:
    f = tmp_path / "p.toml"
    f.write_text(text)
    with pytest.raises(ConfigError, match=r"notify"):
        load(f)


def _summary_cfg(tmp_path: Path, body: str) -> Config:
    f = tmp_path / "p.toml"
    f.write_text(body)
    return load(f)


def test_summary_defaults(tmp_path: Path) -> None:
    cfg = _summary_cfg(tmp_path, "")
    assert cfg.summary == SummarySettings(monthly_limit_usd=5.0)
    assert cfg.summary.enabled


def test_summary_limit_zero_disables(tmp_path: Path) -> None:
    assert not _summary_cfg(tmp_path, "[summary]\nmonthly_limit_usd = 0\n").summary.enabled


def test_summary_limit_accepts_int_and_float(tmp_path: Path) -> None:
    assert (
        _summary_cfg(tmp_path, "[summary]\nmonthly_limit_usd = 2\n").summary.monthly_limit_usd
        == 2.0
    )
    assert (
        _summary_cfg(tmp_path, "[summary]\nmonthly_limit_usd = 0.5\n").summary.monthly_limit_usd
        == 0.5
    )


@pytest.mark.parametrize(
    "body",
    [
        "[summary]\nmonthly_limit_usd = -1\n",
        '[summary]\nmonthly_limit_usd = "5"\n',
        "[summary]\nmonthly_limit_usd = true\n",
        "summary = 3\n",
    ],
)
def test_summary_rejects_bad_values(tmp_path: Path, body: str) -> None:
    with pytest.raises(ConfigError, match=r"\[summary\]"):
        _summary_cfg(tmp_path, body)


def test_anthropic_key_path() -> None:
    assert anthropic_key_path({"XDG_CONFIG_HOME": "/x"}) == Path("/x/cardak/anthropic_key")
