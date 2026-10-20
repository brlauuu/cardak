"""Atomic writes for every file cardak owns."""

import os
import tempfile
from pathlib import Path


def write_atomic(target: Path, text: str) -> None:
    """Replace target with text via a temp file beside it; keep its mode (new files: 0644).

    Callers pass the resolved path, so a symlink's target is written, not the link replaced.
    Raises OSError.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    mode = target.stat().st_mode & 0o777 if target.exists() else 0o644
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
