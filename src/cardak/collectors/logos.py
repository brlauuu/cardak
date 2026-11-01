"""The images a project's README shows, in order: where a project's logo usually is (#9)."""

import re
from pathlib import Path

READ_LIMIT = 256 * 1024  # a logo is near the top; a huge README isn't read whole
README_NAMES = ("readme.md", "readme.rst", "readme")
_IMAGES = re.compile(
    r"""<img\b[^>]*?\bsrc\s*=\s*["']([^"']+)["']|!\[[^\]]*\]\(\s*<?([^)\s>]+)""",
    re.IGNORECASE,
)


def _readme(project: Path) -> Path | None:
    try:
        entries = {p.name.lower(): p for p in project.iterdir() if p.is_file()}
    except OSError:
        return None
    return next((entries[n] for n in README_NAMES if n in entries), None)


def _local(target: str) -> str | None:
    target = re.split(r"[#?]", target, maxsplit=1)[0]
    if not target or target.startswith("/") or re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I):
        return None
    return target.removeprefix("./")


def readme_images(project: Path) -> tuple[str, ...]:
    """Relative image paths in the project's README (`<img src>` and `![](…)`), in order."""
    readme = _readme(project)
    if readme is None:
        return ()
    try:
        with readme.open("rb") as f:
            text = f.read(READ_LIMIT).decode("utf-8", errors="replace")
    except OSError:
        return ()
    found = (_local(m.group(1) or m.group(2)) for m in _IMAGES.finditer(text))
    return tuple(dict.fromkeys(t for t in found if t))
