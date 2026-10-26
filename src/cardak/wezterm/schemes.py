"""List WezTerm's built-in color schemes by evaluating a probe config headlessly.

`wezterm --config-file <probe> ls-fonts` loads the config (running the probe) without
opening a window. The result is cached per WezTerm version.
"""

import json
import os
import re
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path

from cardak.core.themes import Scheme

Runner = Callable[..., subprocess.CompletedProcess[str]]
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")

PROBE = """local wezterm = require 'wezterm'
local out = {}
for name, c in pairs(wezterm.color.get_builtin_schemes()) do
  out[name] = { background = c.background, foreground = c.foreground,
                cursor_bg = c.cursor_bg, ansi = c.ansi }
end
local f = assert(io.open(os.getenv('CARDAK_SCHEMES_OUT'), 'w'))
f:write(wezterm.json_encode(out))
f:close()
return {}
"""


class SchemesUnavailable(Exception):
    pass


def default_cache_dir(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    if env.get("XDG_CACHE_HOME"):
        return Path(env["XDG_CACHE_HOME"]) / "cardak"
    return Path(env.get("HOME") or Path.home()) / ".cache" / "cardak"


def _hex(value: object) -> str | None:
    return value if isinstance(value, str) and HEX.match(value) else None


def _scheme(name: object, colors: object) -> Scheme | None:
    """One probe entry, or None when it isn't usable (e.g. `rgba(...)` instead of hex)."""
    if not isinstance(name, str) or not isinstance(colors, dict):
        return None
    background, foreground = _hex(colors.get("background")), _hex(colors.get("foreground"))
    ansi = colors.get("ansi")
    if background is None or foreground is None or not isinstance(ansi, list):
        return None
    ansi_hex = tuple(a for a in ansi if _hex(a))
    if len(ansi_hex) != len(ansi):
        return None
    return Scheme(name, background, foreground, _hex(colors.get("cursor_bg")), ansi_hex)


def parse_schemes(data: object) -> dict[str, Scheme]:
    if not isinstance(data, dict):
        raise SchemesUnavailable("unexpected probe output")
    parsed = (_scheme(name, colors) for name, colors in sorted(data.items()))
    return {s.name: s for s in parsed if s is not None}


def _version(runner: Runner) -> str:
    try:
        out = runner(["wezterm", "--version"], capture_output=True, text=True, timeout=5)
    except FileNotFoundError:
        raise SchemesUnavailable("wezterm is not installed") from None
    except subprocess.TimeoutExpired:
        raise SchemesUnavailable("wezterm --version timed out") from None
    except OSError as e:
        raise SchemesUnavailable(f"cannot run wezterm: {e}") from None
    words = out.stdout.split()
    return re.sub(r"[^A-Za-z0-9._-]", "_", words[-1]) if words else "unknown"


def _cache_file(cache_dir: Path, runner: Runner) -> Path:
    return cache_dir / f"schemes-{_version(runner)}.json"


def read_cached_schemes(
    cache_dir: Path, runner: Runner = subprocess.run
) -> dict[str, Scheme] | None:
    """The cached list for the installed WezTerm, without ever running the probe."""
    try:
        cached = parse_schemes(json.loads(_cache_file(cache_dir, runner).read_text()))
    except (OSError, ValueError, SchemesUnavailable):
        return None
    return cached or None  # an empty list is no list: the picker would stay empty (#24)


def _probe(runner: Runner) -> object:
    with tempfile.TemporaryDirectory(prefix="cardak-schemes-") as tmp:
        probe, out = Path(tmp) / "probe.lua", Path(tmp) / "schemes.json"
        probe.write_text(PROBE)
        try:
            done = runner(
                ["wezterm", "--config-file", str(probe), "ls-fonts", "--list-system"],
                capture_output=True,
                text=True,
                timeout=20,
                env={**os.environ, "CARDAK_SCHEMES_OUT": str(out)},
            )
        except FileNotFoundError:
            raise SchemesUnavailable("wezterm is not installed") from None
        except subprocess.TimeoutExpired:
            raise SchemesUnavailable("wezterm timed out listing schemes") from None
        except OSError as e:
            raise SchemesUnavailable(f"cannot run wezterm: {e}") from None
        try:
            raw = out.read_text()
        except OSError:
            raise SchemesUnavailable(
                f"wezterm did not write the scheme list (exit {done.returncode})"
            ) from None
    try:
        return json.loads(raw)
    except ValueError:
        raise SchemesUnavailable("unexpected probe output") from None


def load_schemes(cache_dir: Path, runner: Runner = subprocess.run) -> dict[str, Scheme]:
    cache = _cache_file(cache_dir, runner)
    try:
        if cached := parse_schemes(json.loads(cache.read_text())):
            return cached
    except (OSError, ValueError, SchemesUnavailable):
        pass
    data = _probe(runner)
    schemes = parse_schemes(data)
    if not schemes:
        raise SchemesUnavailable("wezterm returned no schemes")
    try:  # the cache is an optimisation; failing to write it is not an error
        cache_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=cache_dir, prefix=".schemes.", suffix=".json")
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.replace(tmp, cache)
    except OSError:
        pass
    return schemes
