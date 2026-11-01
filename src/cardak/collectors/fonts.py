"""The system's monospace font, for drawing a project's ASCII art into an image (#9)."""

import subprocess
from collections.abc import Callable
from pathlib import Path

Runner = Callable[..., subprocess.CompletedProcess[str]]
TIMEOUT = 3


def monospace_font(runner: Runner = subprocess.run) -> Path | None:
    """The file fontconfig picks for "monospace"; None without fontconfig or a usable answer."""
    try:
        out = runner(
            ["fc-match", "-f", "%{file}", "monospace"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    path = Path(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip() else None
    return path if path is not None and path.is_file() else None
