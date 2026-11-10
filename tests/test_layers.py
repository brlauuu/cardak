"""The layering in AGENTS.md, checked: what each package may import from cardak (#137)."""

import ast
from pathlib import Path

SRC = Path(__file__).parent.parent / "src" / "cardak"

# package -> the cardak packages it may import besides its own
ALLOWED: dict[str, set[str]] = {
    "shared": set(),  # stdlib only; every layer may import it
    "collectors": {"shared"},
    "wezterm": {"shared", "core", "collectors"},  # above core: it writes core's config out
    "claude": {"shared"},
    "llm": {"shared"},
    "systemd": {"shared"},
    "core": {"shared", "collectors"},
}


# modules held tighter than their package: the Lua module's installer only the shared helpers
ONLY: dict[str, set[str]] = {"wezterm/install.py": {"shared"}}


def _imports(path: Path) -> set[str]:
    """The cardak subpackages a module imports, at any depth (lazy imports count)."""
    found = set()
    for node in ast.walk(ast.parse(path.read_text())):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module]
        for name in names:
            parts = name.split(".")
            if parts[0] == "cardak" and len(parts) > 1:
                found.add(parts[1])
    return found


def test_each_layer_imports_only_what_it_may() -> None:
    wrong = []
    for package, allowed in ALLOWED.items():
        for module in (SRC / package).rglob("*.py"):
            own = ONLY.get(str(module.relative_to(SRC)), allowed)
            extra = _imports(module) - own - {package}
            if extra:
                wrong.append(f"{module.relative_to(SRC)} imports {sorted(extra)}")
    assert not wrong, wrong


def test_the_check_sees_an_import() -> None:
    assert _imports(SRC / "core" / "limits.py") >= {"shared", "collectors", "core"}
