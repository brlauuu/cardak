"""The web view's key: a secret every data request carries (M7)."""

import os
import secrets
import tempfile
from pathlib import Path


class WebKeyError(Exception):
    pass


def read_key(path: Path) -> str:
    """The key; refused when missing, empty, or readable by group or others."""
    try:
        mode = path.stat().st_mode
        key = path.read_text(encoding="utf-8").strip()
    except OSError as e:
        raise WebKeyError(f"{path}: {e.strerror or e}") from e
    if mode & 0o077:
        raise WebKeyError(f"{path} is readable by others; run chmod 600 {path}")
    if not key:
        raise WebKeyError(f"{path} is empty; run cardak web setup")
    return key


def ensure_key(path: Path, new: bool = False) -> tuple[str, bool]:
    """The existing key, or a new one written with mode 0600; (key, created)."""
    if not new:
        try:
            return read_key(path), False
        except WebKeyError:
            if path.exists() and path.read_text(encoding="utf-8").strip():
                raise  # an open but real key: let the user fix it rather than replace it
    key = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".web_key.")  # created 0600
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(key + "\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return key, True
