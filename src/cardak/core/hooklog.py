"""hook.log: where the commands Claude Code runs (`hook`, `summarize`, `statusline`) report
problems, since they print nothing and always succeed."""

import time
from collections.abc import Mapping

from cardak.core.config import state_dir

LOG_MAX = 1_000_000  # bytes; past this the log starts over


def log_problem(env: Mapping[str, str], message: str) -> None:
    log = state_dir(env) / "hook.log"
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        mode = "w" if log.exists() and log.stat().st_size > LOG_MAX else "a"
        with log.open(mode, encoding="utf-8") as f:
            f.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')}\n{message}\n")
    except OSError:
        pass  # nowhere left to report it; never fail the hook
