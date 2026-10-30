from pathlib import Path

import pytest

from cardak.core.config import (
    THEMES,
    Config,
    ConfigError,
    NotifySettings,
    Project,
    ProviderSettings,
    ResourceSettings,
    SummarySettings,
    WebSettings,
    add_project,
    config_dir,
    config_path,
    key_file,
    load,
    remove_provider,
    set_project_theme,
    set_provider,
    set_summary_provider,
    set_web_enabled,
    summary_ready,
    web_key_path,
    write_resource_defaults,
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
        "[notify]\nmemory_sound = 2\n",
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


def test_key_file() -> None:
    assert key_file(config_dir({"XDG_CONFIG_HOME": "/x"}), "anthropic") == Path(
        "/x/cardak/anthropic_key"
    )
    assert key_file(Path("/k"), "fireworks") == Path("/k/fireworks_key")


def test_summary_defaults_to_anthropic(tmp_path: Path) -> None:
    cfg = _summary_cfg(tmp_path, "[summary]\nmonthly_limit_usd = 3\n")
    assert cfg.summary.provider == "anthropic" and cfg.summary.error is None
    assert cfg.summary.active == ProviderSettings()


def test_summary_provider_tables(tmp_path: Path) -> None:
    body = '[summary]\nprovider = "ollama"\n[summary.ollama]\nmodel = "m"\n'
    cfg = _summary_cfg(tmp_path, body)
    assert cfg.summary.active == ProviderSettings(url="http://127.0.0.1:11434", model="m")
    assert cfg.summary.enabled


def test_summary_fireworks_url_is_fixed(tmp_path: Path) -> None:
    body = '[summary]\nprovider = "fireworks"\n[summary.fireworks]\nmodel = "f"\nurl = "x"\n'
    active = _summary_cfg(tmp_path, body).summary.active
    assert active == ProviderSettings(url="https://api.fireworks.ai/inference/v1", model="f")


@pytest.mark.parametrize(
    "text",
    [
        '[summary]\nprovider = ["ollama", "codex"]\n',
        '[summary]\nprovider = "gpt"\n',
        '[summary]\nprovider = "fireworks"\n',
        '[summary]\nprovider = "openai"\n[summary.openai]\nmodel = "m"\n',
        '[summary]\nprovider = "ollama"\n[summary.ollama]\nurl = "u"\n',
        "[summary.ollama]\nmodel = 3\n",
        '[summary]\nollama = "x"\n',
    ],
)
def test_bad_provider_keeps_projects(tmp_path: Path, text: str) -> None:
    cfg = _summary_cfg(tmp_path, '[[project]]\nname = "webshop"\npath = "/w"\n' + text)
    assert cfg.projects[0].name == "webshop"
    assert cfg.summary.error and not cfg.summary.enabled


def test_set_provider_keeps_comments(tmp_path: Path) -> None:
    path = tmp_path / "p.toml"
    path.write_text("# mine\n[summary] # cap\nmonthly_limit_usd = 2\n")
    set_provider(path, "ollama", None, "gemma4:e4b")
    set_summary_provider(path, "ollama", tmp_path)
    text = path.read_text()
    assert "# mine" in text and "# cap" in text
    cfg = load(path)
    assert cfg.summary.provider == "ollama" and cfg.summary.monthly_limit_usd == 2.0
    assert cfg.summary.active.model == "gemma4:e4b"


def test_set_provider_on_empty_file_and_replace(tmp_path: Path) -> None:
    path = tmp_path / "p.toml"
    set_provider(path, "openai", "http://127.0.0.1:8080/v1", "a")
    set_provider(path, "openai", "http://127.0.0.1:8080/v1", "b")
    assert load(path).summary.providers["openai"].model == "b"


def test_set_summary_provider_refuses_unset(tmp_path: Path) -> None:
    path = tmp_path / "p.toml"
    path.write_text("# mine\n")
    keys = tmp_path / "cfg"
    with pytest.raises(ConfigError, match="codex"):
        set_summary_provider(path, "codex", keys)
    with pytest.raises(ConfigError, match="anthropic"):
        set_summary_provider(path, "anthropic", keys)  # no key file
    with pytest.raises(ConfigError, match="nope"):
        set_summary_provider(path, "nope", keys)
    assert path.read_text() == "# mine\n"
    keys.mkdir()
    (keys / "anthropic_key").write_text("k")
    set_summary_provider(path, "anthropic", keys)
    assert load(path).summary.provider == "anthropic"


def test_remove_provider(tmp_path: Path) -> None:
    path = tmp_path / "p.toml"
    set_provider(path, "codex", None, None)
    set_provider(path, "ollama", None, "m")
    set_summary_provider(path, "codex", tmp_path)
    with pytest.raises(ConfigError, match="in use"):
        remove_provider(path, "codex")
    remove_provider(path, "ollama")
    remove_provider(path, "ollama")  # already gone: nothing to do
    assert set(load(path).summary.providers) == {"codex"}


def test_summary_ready(tmp_path: Path) -> None:
    keys = tmp_path / "cfg"

    def ready(body: str) -> bool:
        return summary_ready(_summary_cfg(tmp_path, body).summary, keys)

    assert not ready("")  # anthropic, no key
    assert ready('[summary]\nprovider = "ollama"\n[summary.ollama]\nmodel = "m"\n')
    assert ready('[summary]\nprovider = "codex"\n[summary.codex]\n')
    fw = '[summary]\nprovider = "fireworks"\n[summary.fireworks]\nmodel = "m"\n'
    assert not ready(fw)
    oa = '[summary]\nprovider = "openai"\n[summary.openai]\nurl = "u"\nmodel = "m"\n'
    assert ready(oa)
    keys.mkdir()
    (keys / "fireworks_key").write_text("k")
    assert ready(fw)
    assert not ready(fw.replace("[summary]\n", "[summary]\nmonthly_limit_usd = 0\n"))
    assert not ready('[summary]\nprovider = "gpt"\n')


def test_resources_default_to_derived(tmp_path: Path) -> None:
    assert _summary_cfg(tmp_path, "").resources == ResourceSettings()


def test_resources_section_parsed(tmp_path: Path) -> None:
    cfg = _summary_cfg(tmp_path, "[resources]\npane_ram_gb = 10.5\nsustain_s = 60\n")
    assert cfg.resources == ResourceSettings(pane_ram_gb=10.5, sustain_s=60.0)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("[resources]\npane_ram_gb = -1\n", r"\[resources\] pane_ram_gb must be a number > 0"),
        ("[resources]\npane_ram_gb = 0\n", r"\[resources\] pane_ram_gb must be a number > 0"),
        ("[resources]\npane_ram_gb = true\n", r"\[resources\] pane_ram_gb must be a number > 0"),
        ('[resources]\nsustain_s = "1"\n', r"\[resources\] sustain_s must be a number > 0"),
        (
            "[resources]\nmachine_cpu_pct = 150\n",
            r"\[resources\] machine_cpu_pct must be a number > 0 and ≤ 100",
        ),
        ("resources = 3\n", r"\[resources\] must be a table"),
    ],
)
def test_resources_rejects_bad_values(tmp_path: Path, body: str, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        _summary_cfg(tmp_path, body)


RESOURCE_ENTRIES = [
    ("ram_warn_available_gb", 4.8, "15% of 32 GB"),
    ("pane_ram_gb", 8.0, "25% of 32 GB"),
    ("pane_cpu_pct", 800.0, "half of 16 cores"),
    ("growth_gb_per_min", 1.0, ""),
]
MINE = """\
# my projects
[[project]]
name = "webshop"
path = "/r/webshop"

[resources]
pane_ram_gb = 4 # mine
"""


def test_write_resource_defaults_adds_missing_keys_only(tmp_path: Path) -> None:
    f = tmp_path / "p.toml"
    f.write_text(MINE)
    written = write_resource_defaults(f, RESOURCE_ENTRIES)
    assert written == ["ram_warn_available_gb", "pane_cpu_pct", "growth_gb_per_min"]
    text = f.read_text()
    assert text.startswith(MINE.rstrip("\n"))  # the user's text is untouched
    assert "pane_ram_gb = 4 # mine" in text
    assert "ram_warn_available_gb = 4.8 # 15% of 32 GB" in text
    assert "pane_cpu_pct = 800 # half of 16 cores" in text
    assert "growth_gb_per_min = 1\n" in text or "growth_gb_per_min = 1.0\n" in text
    cfg = load(f)
    assert cfg.resources.pane_ram_gb == 4.0 and cfg.resources.ram_warn_available_gb == 4.8
    assert [p.name for p in cfg.projects] == ["webshop"]


def test_write_resource_defaults_refresh_overwrites(tmp_path: Path) -> None:
    f = tmp_path / "p.toml"
    f.write_text(MINE)
    write_resource_defaults(f, RESOURCE_ENTRIES, refresh=True)
    assert load(f).resources.pane_ram_gb == 8.0
    assert f.read_text().startswith("# my projects\n[[project]]")


def test_write_resource_defaults_creates_file_and_table(tmp_path: Path) -> None:
    f = tmp_path / "new" / "p.toml"
    assert write_resource_defaults(f, RESOURCE_ENTRIES) == [k for k, _, _ in RESOURCE_ENTRIES]
    assert load(f).resources.pane_cpu_pct == 800.0
    assert f.read_text().startswith("[resources]\n")


def test_write_resource_defaults_after_projects_only(tmp_path: Path) -> None:
    f = tmp_path / "p.toml"
    f.write_text('[[project]]\nname = "webshop"\npath = "/r/webshop"\n')
    write_resource_defaults(f, RESOURCE_ENTRIES)
    cfg = load(f)
    assert [p.name for p in cfg.projects] == ["webshop"] and cfg.resources.pane_ram_gb == 8.0


def test_write_resource_defaults_twice_writes_nothing(tmp_path: Path) -> None:
    f = tmp_path / "p.toml"
    write_resource_defaults(f, RESOURCE_ENTRIES)
    before = f.stat().st_mtime_ns
    assert write_resource_defaults(f, RESOURCE_ENTRIES) == []
    assert f.stat().st_mtime_ns == before


def test_write_resource_defaults_refuses_a_broken_file(tmp_path: Path) -> None:
    f = tmp_path / "p.toml"
    f.write_text("[[project]]\nname = \n")
    with pytest.raises(ConfigError):
        write_resource_defaults(f, RESOURCE_ENTRIES)


def test_web_defaults(tmp_path: Path) -> None:
    p = tmp_path / "projects.toml"
    p.write_text(SAMPLE)
    assert load(p).web == WebSettings()
    assert WebSettings() == WebSettings(enabled=False, port=8765, host="0.0.0.0")


def test_web_section_parsed(tmp_path: Path) -> None:
    p = tmp_path / "projects.toml"
    p.write_text('[web]\nenabled = true\nport = 9000\nhost = "192.168.1.5"\n')
    assert load(p).web == WebSettings(True, 9000, "192.168.1.5")


@pytest.mark.parametrize(
    "body", ['enabled = "yes"', "port = 0", "port = 70000", "port = true", "host = 5"]
)
def test_web_bad_values_raise(tmp_path: Path, body: str) -> None:
    p = tmp_path / "projects.toml"
    p.write_text(f"[web]\n{body}\n")
    with pytest.raises(ConfigError, match=r"\[web\]"):
        load(p)


def test_set_web_enabled_keeps_comments(tmp_path: Path) -> None:
    p = tmp_path / "projects.toml"
    p.write_text(SAMPLE)
    set_web_enabled(p, True)
    assert "# my projects" in p.read_text()
    assert load(p).web.enabled
    assert len(load(p).projects) == 2
    set_web_enabled(p, False)
    assert not load(p).web.enabled
    assert "# my projects" in p.read_text()


def test_web_key_path(tmp_path: Path) -> None:
    assert web_key_path({"XDG_CONFIG_HOME": str(tmp_path)}) == tmp_path / "cardak" / "web_key"


def test_theme_keys_parsed(tmp_path: Path) -> None:
    f = tmp_path / "p.toml"
    f.write_text('[notify]\nwaiting_sound = "peon/A"\nmemory_sound = "off"\nother_sound = "bell"\n')
    n = load(f).notify
    assert [n.theme_sound(t) for t in THEMES] == ["peon/A", "off", "bell"]
    assert n.theme_key("waiting") == "waiting_sound"
    assert n.theme_key("memory") == "memory_sound"


def test_theme_defaults() -> None:
    n = NotifySettings()
    assert [n.theme_sound(t) for t in THEMES] == ["random"] * 3
    assert n.theme_key("waiting") == "sound"


def test_legacy_sound_means_waiting() -> None:
    n = NotifySettings(sound="bell")
    assert [n.theme_sound(t) for t in THEMES] == ["bell", "random", "random"]


def test_legacy_off_silences_all() -> None:
    n = NotifySettings(sound="off")
    assert [n.theme_sound(t) for t in THEMES] == ["off"] * 3


def test_legacy_off_with_one_theme_key() -> None:
    n = NotifySettings(sound="off", memory_sound="peon/X")
    assert [n.theme_sound(t) for t in THEMES] == ["off", "peon/X", "random"]


def test_bad_provider_keeps_the_valid_tables(tmp_path: Path) -> None:
    body = (
        '[summary]\nprovider = "nope"\n'
        '[summary.ollama]\nmodel = "m"\n[summary.openai]\nmodel = "x"\n'
    )
    summary = _summary_cfg(tmp_path, body).summary
    assert summary.error and not summary.enabled
    assert set(summary.providers) == {"ollama"}  # openai has no url: left out, ollama still usable
    path = tmp_path / "p.toml"
    set_summary_provider(path, "ollama", tmp_path)
    assert load(path).summary.provider == "ollama"


def test_dotted_summary_keys_are_refused_not_crashed(tmp_path: Path) -> None:
    path = tmp_path / "p.toml"
    path.write_text("summary.monthly_limit_usd = 3\n\n[summary.codex]\n")
    with pytest.raises(ConfigError, match="by hand"):  # tomlkit hands back a plain dict here
        set_summary_provider(path, "codex", tmp_path)
