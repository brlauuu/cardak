"""Load ~/.config/cardak/projects.toml and append projects to it."""

import os
import re
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from tomlkit import TOMLDocument
    from tomlkit.items import Table

# tomli_w and tomlkit are imported where projects.toml is written: `cardak hook` loads this
# module on every Claude Code hook and only reads.


@dataclass(frozen=True)
class Project:
    name: str
    path: Path
    theme: str | None = None
    accent: str | None = None
    github: str | None = None
    tint: str | None = None


Theme = Literal["waiting", "memory", "other"]  # which notification a sound is for (#91)
THEMES: tuple[Theme, ...] = ("waiting", "memory", "other")


@dataclass(frozen=True)
class NotifySettings:
    desktop: bool = True  # notify-send when a session starts waiting
    sound: str = "random"  # "random" | "off" | a bundled name | a file name in sounds_dir
    sounds_dir: Path | None = None
    # per theme, the same values plus "<pack>/<clip>"; unset follows `sound` (see theme_sound)
    waiting_sound: str | None = None
    memory_sound: str | None = None
    other_sound: str | None = None

    def theme_sound(self, theme: Theme) -> str:
        """`sound` predates the themes: it stands for the waiting one, and an "off" with no
        theme key set keeps every theme silent."""
        if theme == "waiting":
            return self.waiting_sound or self.sound
        own = self.memory_sound if theme == "memory" else self.other_sound
        if own is not None:
            return own
        untouched = self.waiting_sound is self.memory_sound is self.other_sound is None
        return "off" if self.sound == "off" and untouched else "random"

    def theme_key(self, theme: Theme) -> str:
        """The [notify] key a theme's setting came from, for warnings."""
        if theme == "waiting" and self.waiting_sound is None:
            return "sound"
        return f"{theme}_sound"


PROVIDERS = ("anthropic", "ollama", "fireworks", "openai", "codex")
KEYED = {"anthropic": True, "fireworks": True, "openai": False}  # True: the key is required
OLLAMA_URL = "http://127.0.0.1:11434"
FIREWORKS_URL = "https://api.fireworks.ai/inference/v1"
DEFAULT_URLS = {"ollama": OLLAMA_URL, "fireworks": FIREWORKS_URL}  # fireworks' can't be changed
NEEDS = {"ollama": ("model",), "fireworks": ("model",), "openai": ("url", "model")}


@dataclass(frozen=True)
class ProviderSettings:
    url: str | None = None
    model: str | None = None


@dataclass(frozen=True)
class SummarySettings:
    monthly_limit_usd: float = 5.0  # 0 turns waiting summaries off
    provider: str = "anthropic"
    providers: Mapping[str, ProviderSettings] = field(default_factory=dict)
    error: str | None = None  # a provider problem: summaries are off, the rest of the file stands

    @property
    def enabled(self) -> bool:
        return self.monthly_limit_usd > 0 and self.error is None

    @property
    def active(self) -> ProviderSettings:
        return self.providers.get(self.provider, ProviderSettings())


@dataclass(frozen=True)
class ResourceSettings:
    """[resources]: every None means "derive from the machine" (core/resources.py)."""

    ram_warn_available_gb: float | None = None
    ram_critical_available_gb: float | None = None
    pane_ram_gb: float | None = None
    growth_gb_per_min: float | None = None
    pane_cpu_pct: float | None = None
    machine_cpu_pct: float | None = None
    sustain_s: float | None = None
    repeat_min: float | None = None


@dataclass(frozen=True)
class WebSettings:
    """[web]: the read-only page `cardak daemon` serves to phones on the home network (M7)."""

    enabled: bool = False
    port: int = 8765
    host: str = "0.0.0.0"  # every interface


@dataclass(frozen=True)
class Config:
    projects: tuple[Project, ...] = ()
    defaults: dict[str, object] = field(default_factory=dict)
    notify: NotifySettings = NotifySettings()
    summary: SummarySettings = SummarySettings()
    resources: ResourceSettings = ResourceSettings()
    web: WebSettings = WebSettings()


class ConfigError(Exception):
    def __init__(self, path: Path, message: str, line: int | None = None) -> None:
        self.path = path
        self.message = message
        self.line = line
        where = f"{path.name} line {line}" if line else path.name
        super().__init__(f"{where}: {message}")


def real_path(path: Path) -> Path:
    """path with symlinks resolved; as given when it can't be (missing, a loop, no access)."""
    try:
        return path.resolve()
    except (OSError, RuntimeError):
        return path


def config_dir(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    if env.get("XDG_CONFIG_HOME"):
        return Path(env["XDG_CONFIG_HOME"]) / "cardak"
    return Path(env.get("HOME") or Path.home()) / ".config" / "cardak"


def key_file(keys_dir: Path, name: str) -> Path:
    """A summary provider's API key (`anthropic_key`, `fireworks_key`, …) in a keys dir:
    config_dir, or a test's. The user or the Summaries screen writes it; never projects.toml."""
    return keys_dir / f"{name}_key"


def is_set_up(summary: SummarySettings, name: str, keys_dir: Path) -> bool:
    """anthropic once its key exists (it needs no table); any other once it has a table."""
    if name == "anthropic":
        return key_file(keys_dir, name).exists()
    return name in summary.providers


def summary_ready(summary: SummarySettings, keys_dir: Path) -> bool:
    """Whether a summary could be made now: config and a stat, cheap enough for the hook."""
    if not summary.enabled:
        return False
    return not KEYED.get(summary.provider) or key_file(keys_dir, summary.provider).exists()


def web_key_path(env: Mapping[str, str] | None = None) -> Path:
    """The web view's key; `cardak web setup` writes it, `cardak daemon` reads it."""
    return config_dir(env) / "web_key"


def state_dir(env: Mapping[str, str] | None = None) -> Path:
    """Where cardak keeps runtime state: the agent database, focus history, hook log."""
    env = os.environ if env is None else env
    if env.get("XDG_STATE_HOME"):
        return Path(env["XDG_STATE_HOME"]) / "cardak"
    return Path(env.get("HOME") or Path.home()) / ".local" / "state" / "cardak"


def cache_dir(env: Mapping[str, str] | None = None) -> Path:
    """Where cardak keeps what it can download again: the Claude price list."""
    env = os.environ if env is None else env
    if env.get("XDG_CACHE_HOME"):
        return Path(env["XDG_CACHE_HOME"]) / "cardak"
    return Path(env.get("HOME") or Path.home()) / ".cache" / "cardak"


def config_path(cli_value: str | None = None, env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    if cli_value:
        return Path(cli_value).expanduser()
    if env.get("CARDAK_CONFIG"):
        return Path(env["CARDAK_CONFIG"]).expanduser()
    return config_dir(env) / "projects.toml"


def _opt_str(entry: Mapping[str, object], key: str) -> str | None:
    value = entry.get(key)
    return value if isinstance(value, str) else None


def _read(path: Path) -> str | None:
    """The file's text, None when it does not exist; any other failure is a ConfigError."""
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as e:
        raise ConfigError(path, f"cannot read: {e}") from e


def _parse(path: Path, text: str) -> dict[str, object]:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        found = re.search(r"at line (\d+)", str(e))
        message = re.sub(r"\s*\(at line \d+, column \d+\)$", "", str(e))
        raise ConfigError(path, message, int(found.group(1)) if found else None) from e


def load(path: Path) -> Config:
    text = _read(path)
    if text is None:
        return Config()
    data = _parse(path, text)

    raw = data.get("project", [])
    if not isinstance(raw, list):
        raise ConfigError(path, "[[project]] must be an array of tables")
    projects = []
    for i, entry in enumerate(raw, 1):
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("name"), str)
            or not isinstance(entry.get("path"), str)
        ):
            raise ConfigError(path, f"project #{i} needs a string 'name' and 'path'")
        projects.append(
            Project(
                name=entry["name"],
                path=Path(entry["path"]).expanduser(),
                theme=_opt_str(entry, "theme"),
                accent=_opt_str(entry, "accent"),
                github=_opt_str(entry, "github"),
                tint=_opt_str(entry, "tint"),
            )
        )
    defaults = data.get("defaults", {})
    if not isinstance(defaults, dict):
        raise ConfigError(path, "[defaults] must be a table")
    return Config(
        projects=tuple(projects),
        defaults=dict(defaults),
        notify=_notify(path, data),
        summary=_summary(path, data),
        resources=_resources(path, data),
        web=_web(path, data),
    )


def _notify(path: Path, data: Mapping[str, object]) -> NotifySettings:
    raw = data.get("notify", {})
    if not isinstance(raw, dict):
        raise ConfigError(path, "[notify] must be a table")
    desktop, sound, sounds_dir = (
        raw.get("desktop", True),
        raw.get("sound", "random"),
        raw.get("sounds_dir"),
    )
    if not isinstance(desktop, bool):
        raise ConfigError(path, "[notify] desktop must be true or false")
    if not isinstance(sound, str):
        raise ConfigError(path, "[notify] sound must be a string")
    if sounds_dir is not None and not isinstance(sounds_dir, str):
        raise ConfigError(path, "[notify] sounds_dir must be a string")
    return NotifySettings(
        desktop,
        sound,
        Path(sounds_dir).expanduser() if sounds_dir else None,
        *_theme_sounds(path, raw),
    )


def _theme_sounds(path: Path, raw: Mapping[str, object]) -> list[str | None]:
    """`waiting_sound`, `memory_sound`, `other_sound`, in THEMES order (#91)."""
    values: list[str | None] = []
    for t in THEMES:
        value = raw.get(f"{t}_sound")
        if value is not None and not isinstance(value, str):
            raise ConfigError(path, f"[notify] {t}_sound must be a string")
        values.append(value)
    return values


def _summary(path: Path, data: Mapping[str, object]) -> SummarySettings:
    raw = data.get("summary", {})
    if not isinstance(raw, dict):
        raise ConfigError(path, "[summary] must be a table")
    limit = raw.get("monthly_limit_usd", 5.0)
    if isinstance(limit, bool) or not isinstance(limit, int | float) or limit < 0:
        raise ConfigError(path, "[summary] monthly_limit_usd must be a number ≥ 0")
    try:
        provider, providers = _providers(raw)
    except ValueError as e:
        return SummarySettings(float(limit), error=str(e))
    return SummarySettings(float(limit), provider, providers)


def _providers(raw: Mapping[str, object]) -> tuple[str, dict[str, ProviderSettings]]:
    """The provider in use and every [summary.<name>] table; ValueError says what's wrong."""
    providers = {}
    for name in PROVIDERS:
        if name in raw:
            providers[name] = _provider(name, raw[name])
    provider = raw.get("provider", "anthropic")
    if not isinstance(provider, str):
        raise ValueError("[summary] provider must be one name: only one provider is in use")
    if provider not in PROVIDERS:
        raise ValueError(f"[summary] provider {provider!r} is not one of {', '.join(PROVIDERS)}")
    if provider != "anthropic" and provider not in providers:
        raise ValueError(f"[summary] provider is {provider!r} but [summary.{provider}] is missing")
    return provider, providers


def _provider(name: str, raw: object) -> ProviderSettings:
    if not isinstance(raw, dict):
        raise ValueError(f"[summary.{name}] must be a table")
    for key in ("url", "model"):
        if key in raw and not isinstance(raw[key], str):
            raise ValueError(f"[summary.{name}] {key} must be a string")
    settings = ProviderSettings(DEFAULT_URLS.get(name, raw.get("url")), raw.get("model"))
    if name == "ollama" and raw.get("url"):
        settings = replace(settings, url=raw["url"])
    for needed in NEEDS.get(name, ()):
        if not getattr(settings, needed):
            raise ValueError(f"[summary.{name}] needs a {needed}")
    return settings


def _resources(path: Path, data: Mapping[str, object]) -> ResourceSettings:
    raw = data.get("resources", {})
    if not isinstance(raw, dict):
        raise ConfigError(path, "[resources] must be a table")
    names = ResourceSettings.__dataclass_fields__
    return ResourceSettings(
        **{name: _resource(path, name, raw[name]) for name in names if raw.get(name) is not None}
    )


def _web(path: Path, data: Mapping[str, object]) -> WebSettings:
    raw = data.get("web", {})
    if not isinstance(raw, dict):
        raise ConfigError(path, "[web] must be a table")
    enabled, port, host = (
        raw.get("enabled", False),
        raw.get("port", 8765),
        raw.get("host", "0.0.0.0"),
    )
    if not isinstance(enabled, bool):
        raise ConfigError(path, "[web] enabled must be true or false")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ConfigError(path, "[web] port must be a number from 1 to 65535")
    if not isinstance(host, str) or not host:
        raise ConfigError(path, "[web] host must be a string")
    return WebSettings(enabled, port, host)


def _resource(path: Path, name: str, value: object) -> float:
    percent = name == "machine_cpu_pct"
    limit = " and ≤ 100" if percent else ""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigError(path, f"[resources] {name} must be a number > 0{limit}")
    if value <= 0 or (percent and value > 100):
        raise ConfigError(path, f"[resources] {name} must be a number > 0{limit}")
    return float(value)


def _with_tilde(path: Path) -> str:
    home = Path.home()
    return f"~/{path.relative_to(home)}" if path.is_relative_to(home) else str(path)


def _appended(existing: str, block: str) -> str:
    if not existing or existing.endswith("\n\n"):
        return existing + block
    return existing + ("\n" if existing.endswith("\n") else "\n\n") + block


def _atomic_write(path: Path, target: Path, text: str) -> None:
    """Replace target with text via a temp file in its directory, keeping its mode."""
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        mode = target.stat().st_mode & 0o777 if target.exists() else 0o644
        import tempfile

        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".projects.", suffix=".toml")
    except OSError as e:
        raise ConfigError(path, f"cannot write: {e}") from e
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, mode)  # mkstemp creates 0600
        os.replace(tmp, target)
    except BaseException as e:
        Path(tmp).unlink(missing_ok=True)
        if isinstance(e, OSError):
            raise ConfigError(path, f"cannot write: {e}") from e
        raise


def add_project(path: Path, name: str, project_path: Path, theme: str | None = None) -> None:
    """Append a [[project]] block, keeping the existing text (and its comments) as is."""
    config = load(path)  # raises ConfigError on a broken file, before anything is written
    project_path = project_path.expanduser()
    if any(p.path == project_path for p in config.projects):
        raise ConfigError(path, f"{project_path} is already configured")
    target = path.resolve()  # write through a symlink (e.g. a dotfiles repo), don't replace it
    # The header is written by hand: tomli-w emits an array of tables inline
    # (`project = [...]`), which appended after a table would land inside it.
    entry = {"name": name, "path": _with_tilde(project_path)}
    if theme:
        entry["theme"] = theme
    import tomli_w

    block = "[[project]]\n" + tomli_w.dumps(entry)

    text = _appended(_read(target) or "", block)
    try:
        _parse(path, text)
    except ConfigError:
        # e.g. projects written inline (`project = [...]`) cannot take a [[project]] table
        raise ConfigError(path, "cannot append to this file's layout; add it by hand") from None
    _atomic_write(path, target, text)


def _set_key(table: "Table", key: str, value: object) -> None:
    """Set key in a table; a new key goes after its last key, not after trailing comments.

    tomlkit appends new keys at the very end of a table's body, which places them below a
    blank line or a comment that introduces the *next* table.
    """
    if key in table:
        table[key] = value
        return
    body = table.value.body
    trailing = []
    while body and body[-1][0] is None:  # whitespace and comment items carry no key
        trailing.insert(0, body.pop())
    table.add(key, value)
    body.extend(trailing)


def write_resource_defaults(
    path: Path, entries: Sequence[tuple[str, float, str]], refresh: bool = False
) -> list[str]:
    """Add the [resources] keys that are missing (all of them with refresh), with comments.

    The rest of the file is kept as it is. Returns the keys written; writes nothing if none.
    """
    load(path)  # refuses a broken file before anything is written
    target = path.resolve()
    import tomlkit

    doc = tomlkit.parse(_read(target) or "")
    table = doc.get("resources")
    if table is None:
        table = tomlkit.table()
        doc.add("resources", table)
    written: list[str] = []
    for key, value, comment in entries:
        if key in table and not refresh:
            continue
        item = tomlkit.item(int(value) if float(value).is_integer() else value)
        if comment:
            item.comment(comment)
        _set_key(table, key, item)
        written.append(key)
    if not written:
        return []
    text = tomlkit.dumps(doc)
    _parse(path, text)
    _atomic_write(path, target, text)
    return written


def set_web_enabled(path: Path, enabled: bool) -> None:
    """Set [web] enabled, keeping the rest of the file as it is."""
    load(path)  # refuses a broken file before anything is written
    target = path.resolve()
    import tomlkit

    doc = tomlkit.parse(_read(target) or "")
    table = doc.get("web")
    if table is None:
        table = tomlkit.table()
        doc.add("web", table)
    _set_key(table, "enabled", enabled)
    text = tomlkit.dumps(doc)
    _parse(path, text)
    _atomic_write(path, target, text)


def _summary_doc(path: Path) -> tuple[Path, "TOMLDocument", "Table"]:
    load(path)  # refuses a broken file before anything is written
    target = path.resolve()
    import tomlkit

    doc = tomlkit.parse(_read(target) or "")
    table = doc.get("summary")
    if table is None:
        table = tomlkit.table()
        doc.add("summary", table)
    return target, doc, table


def _write_doc(path: Path, target: Path, doc: "TOMLDocument") -> None:
    import tomlkit

    text = tomlkit.dumps(doc)
    _parse(path, text)
    _atomic_write(path, target, text)


def set_provider(path: Path, name: str, url: str | None, model: str | None) -> None:
    """Write [summary.<name>], replacing what it had; None leaves that key out."""
    import tomlkit

    if name not in PROVIDERS:
        raise ConfigError(path, f"{name!r} is not a summary provider")
    target, doc, summary = _summary_doc(path)
    table = tomlkit.table()
    for key, value in (("url", url), ("model", model)):
        if value:
            table.add(key, value)
    if name in summary:
        summary[name] = table
    else:
        summary.add(name, table)
    _write_doc(path, target, doc)


def set_summary_provider(path: Path, name: str, keys_dir: Path) -> None:
    """Make `name` the provider in use; it must be set up already."""
    if name not in PROVIDERS:
        raise ConfigError(path, f"{name!r} is not a summary provider")
    if not is_set_up(load(path).summary, name, keys_dir):
        raise ConfigError(path, f"{name} isn't set up")
    target, doc, table = _summary_doc(path)
    _set_key(table, "provider", name)
    _write_doc(path, target, doc)


def remove_provider(path: Path, name: str) -> None:
    """Remove [summary.<name>]; its key file stays. The provider in use can't be removed."""
    summary = load(path).summary
    if name == summary.provider:
        raise ConfigError(path, f"{name} is in use; switch to another provider first")
    target, doc, table = _summary_doc(path)
    if name not in table:
        return
    del table[name]
    _write_doc(path, target, doc)


def set_project_theme(path: Path, project_path: Path, theme: str) -> None:
    """Set one project's `theme` in place; comments and layout elsewhere are kept."""
    load(path)  # refuses a broken file before anything is written
    target = path.resolve()
    import tomlkit
    from tomlkit.items import AoT

    doc = tomlkit.parse(_read(target) or "")
    projects = doc.get("project")
    if projects is not None and not isinstance(projects, AoT):
        raise ConfigError(path, "projects are not written as [[project]] tables; edit it by hand")
    project_path = project_path.expanduser()
    for table in projects or []:
        raw = table.get("path")
        if isinstance(raw, str) and Path(raw).expanduser() == project_path:
            _set_key(table, "theme", theme)
            break
    else:
        raise ConfigError(path, f"{project_path} is not a configured project")
    text = tomlkit.dumps(doc)
    _parse(path, text)
    _atomic_write(path, target, text)
