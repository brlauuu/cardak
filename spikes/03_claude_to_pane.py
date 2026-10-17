# /// script
# requires-python = ">=3.12"
# dependencies = ["psutil"]
# ///
"""Spike: map running `claude` processes to WezTerm panes via controlling tty.

Join: pane.tty_name -> processes with that controlling terminal -> `claude` pid
      -> ~/.claude/sessions/<pid>.json (sessionId, cwd, status).
Without WezTerm it still lists every claude process with its tty and session.
"""

import json
import shutil
import subprocess
from pathlib import Path

import psutil

SESSIONS = Path.home() / ".claude" / "sessions"


def wezterm_panes() -> dict[str, dict]:
    if not shutil.which("wezterm"):
        return {}
    out = subprocess.run(["wezterm", "cli", "list", "--format", "json"],
                         capture_output=True, text=True, check=True).stdout
    return {p["tty_name"]: p for p in json.loads(out) if p.get("tty_name")}


def is_claude(proc: psutil.Process) -> bool:
    try:
        cmd = proc.cmdline()
    except psutil.Error:
        return False
    # The CLI shows up as `claude` (native binary) or `node .../claude` (npm install).
    return bool(cmd) and (Path(cmd[0]).name == "claude"
                          or (Path(cmd[0]).name == "node" and any(Path(a).name == "claude" for a in cmd[1:2])))


def session_meta(pid: int) -> dict:
    f = SESSIONS / f"{pid}.json"
    return json.loads(f.read_text()) if f.exists() else {}


def main() -> None:
    panes = wezterm_panes()
    print(f"wezterm panes with tty: {len(panes)}" if panes else "wezterm: not available, listing ttys only")
    for proc in psutil.process_iter(["pid", "terminal", "create_time"]):
        if not is_claude(proc):
            continue
        tty = proc.info["terminal"]
        meta = session_meta(proc.pid)
        pane = panes.get(tty)
        tree = proc.children(recursive=True)
        print(f"\nclaude pid={proc.pid} tty={tty}")
        print(f"  pane      : {pane and {k: pane[k] for k in ('pane_id', 'window_id', 'tab_id', 'workspace')}}")
        print(f"  session   : {meta.get('sessionId')}  name={meta.get('name')}  status={meta.get('status')}")
        print(f"  cwd       : {meta.get('cwd')}  (proc cwd: {proc.cwd()})")
        print(f"  children  : {len(tree)} -> {[c.name() for c in tree][:8]}")


if __name__ == "__main__":
    main()
