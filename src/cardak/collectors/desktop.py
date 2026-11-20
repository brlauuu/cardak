"""The desktop's default app for a file (#182): the one place cardak runs `xdg-open`."""

import subprocess
from collections.abc import Callable
from pathlib import Path

WAIT_S = 3.0  # xdg-open hands over and exits; one that hasn't by then is the app itself


def open_file(
    path: Path,
    wait_s: float = WAIT_S,
    run: Callable[..., "subprocess.Popen[bytes]"] = subprocess.Popen,
) -> str | None:
    """Open a file in the desktop's default app; None, or why it didn't open. Waits up to
    wait_s for xdg-open's verdict, so call it from a worker."""
    if not path.exists():
        return "no such file"
    try:
        proc = run(
            ["xdg-open", str(path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except FileNotFoundError:
        return "xdg-open isn't installed"
    except OSError as e:
        return str(e)
    try:
        code = proc.wait(timeout=wait_s)
    except subprocess.TimeoutExpired:
        return None  # some handlers stay in the foreground: it is open, leave it be
    return None if code == 0 else f"xdg-open failed (exit {code})"
