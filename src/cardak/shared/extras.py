"""Optional extras (#131): which libraries each one adds, and the hint when one is missing.

Asked through importlib.util.find_spec, which imports nothing, so the hook can check one.
"""

import importlib.util

EXTRAS: dict[str, tuple[str, ...]] = {
    "background": ("PIL", "pyfiglet", "resvg_py"),
    "anthropic": ("anthropic",),
}
INSTALL = "uv tool install --reinstall 'cardak[all] @ git+https://github.com/brlauuu/cardak'"

# extra -> installed; a process asks once (an install replaces the tool's environment anyway)
_found: dict[str, bool] = {}


def missing(extra: str) -> bool:
    """Whether any library of that extra can't be imported."""
    if extra not in _found:
        _found[extra] = all(importlib.util.find_spec(m) is not None for m in EXTRAS[extra])
    return not _found[extra]


def needs(extra: str, what: str) -> str:
    return f"{what} needs cardak's {extra} extra: {INSTALL}"
