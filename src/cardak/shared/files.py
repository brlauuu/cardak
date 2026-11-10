"""One atomic write for every file cardak writes (#137)."""

import os
import tempfile
from pathlib import Path


def write_atomic(
    path: Path, data: str | bytes, *, mode: int | None = None, new_mode: int = 0o644
) -> None:
    """Replace the file with data via a temp file beside it, so a reader sees the old file or
    the new one, never half of it. A symlink is written through (dotfiles kept elsewhere), so
    the link stays. The file keeps its mode, or gets new_mode when it is new; `mode` sets it
    whatever it was (key files: 0600). Text is UTF-8. Raises OSError, leaving no temp file."""
    target = Path(os.path.realpath(path))
    target.parent.mkdir(parents=True, exist_ok=True)
    if mode is None:
        mode = target.stat().st_mode & 0o777 if target.exists() else new_mode
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data.encode("utf-8") if isinstance(data, str) else data)
        os.chmod(tmp, mode)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
