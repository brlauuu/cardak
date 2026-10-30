"""`cardak statusline [--then <command>]`, run by Claude Code as its status line command (#70).

It saves the plan limits in the input, then runs the user's own status line command with the same
input and relays its output and exit code, so the status line looks as it did. Wrapping the status
line is a workaround until Claude Code offers another way to read the limits (#97). Like the hook,
it is dispatched before argument parsing, and nothing in cardak's part can stop the user's command
from running: problems go to hook.log.
"""

import os
import signal
import time
import traceback
from collections.abc import Callable, Mapping
from typing import BinaryIO

from cardak.collectors.limits import parse_statusline
from cardak.core.hooklog import log_problem
from cardak.core.limits import limits_path, record

PIPE_MAX = 4096  # an input is ~2 KB; a pipe holds at least one page (pipe-user-pages-soft)


def _save(data: bytes, env: Mapping[str, str], now: float) -> None:
    try:
        reading = parse_statusline(data)
        if reading is not None:
            record(limits_path(env), reading, now)
    except Exception:
        log_problem(env, "statusline: " + traceback.format_exc())


def _exec(command: str, data: bytes) -> int:
    """Become `sh -c command` with `data` as its stdin: its output and exit code are ours, and
    nothing is copied or waited for (a subprocess took ~35 ms more per refresh, measured)."""
    if len(data) > PIPE_MAX:
        import subprocess

        return subprocess.run(["sh", "-c", command], input=data, check=False).returncode
    read, write = os.pipe()
    os.write(write, data)
    os.close(write)
    os.dup2(read, 0)
    os.close(read)
    for sig in (signal.SIGPIPE, signal.SIGXFSZ):  # Python ignores these; exec would keep that
        signal.signal(sig, signal.SIG_DFL)
    os.execvp("sh", ["sh", "-c", command])


def main(
    argv: list[str],
    stdin: BinaryIO,
    env: Mapping[str, str] | None = None,
    clock: Callable[[], float] = time.time,
    run: Callable[[str, bytes], int] | None = None,
) -> int:
    env = os.environ if env is None else env
    data = stdin.read()
    if argv and not (len(argv) == 2 and argv[0] == "--then"):
        log_problem(env, f"statusline: ignored arguments {argv!r}")
        return 0
    _save(data, env, clock())
    return (run or _exec)(argv[1], data) if argv else 0
