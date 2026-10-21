# /// script
# requires-python = ">=3.12"
# ///
"""M4 spike: record Claude Code hook payloads.

Register as the command of every hook in a scratch settings file and run
`claude --setting-sources project --settings <that file>`, so the user's own settings and
hooks are untouched. Each call writes <outdir>/<time_ns>-<event>.json with the payload,
whether WEZTERM_PANE reached the hook, and the hook's process ancestry.
"""

import json
import os
import sys
import time
from pathlib import Path


def ancestry(pid: int, depth: int = 6) -> list[str]:
    names = []
    for _ in range(depth):
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
        except OSError:
            break
        comm = stat[stat.index("(") + 1 : stat.rindex(")")]
        names.append(comm)
        pid = int(stat[stat.rindex(")") + 2 :].split()[1])
        if pid <= 1:
            break
    return names


def main() -> None:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except ValueError:
        payload = {"_raw": raw}
    event = payload.get("hook_event_name", "unknown") if isinstance(payload, dict) else "unknown"
    record = {
        "payload": payload,
        "env": {"WEZTERM_PANE": os.environ.get("WEZTERM_PANE")},
        "ancestry": ancestry(os.getppid()),
    }
    (out / f"{time.time_ns()}-{event}.json").write_text(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
