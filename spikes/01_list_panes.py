# /// script
# requires-python = ">=3.12"
# ///
"""Spike: does `wezterm cli list --format json` give cwd and tty_name for every pane?

Also saves the raw output to spikes/out/ so it can become a test fixture later.
"""

import json
import subprocess
from pathlib import Path

OUT = Path(__file__).parent / "out"


def main() -> None:
    raw = subprocess.run(["wezterm", "cli", "list", "--format", "json"],
                         capture_output=True, text=True, check=True).stdout
    OUT.mkdir(exist_ok=True)
    (OUT / "wezterm_cli_list.json").write_text(raw)
    panes = json.loads(raw)
    print(f"{len(panes)} panes; keys: {sorted(panes[0]) if panes else '-'}\n")
    print(f"{'pane':>4} {'win':>3} {'tab':>3}  {'tty_name':<12} {'cwd':<45} title")
    missing = 0
    for p in panes:
        ok = bool(p.get("cwd")) and bool(p.get("tty_name"))
        missing += not ok
        print(f"{p['pane_id']:>4} {p['window_id']:>3} {p['tab_id']:>3}  {str(p.get('tty_name')):<12} "
              f"{str(p.get('cwd')):<45} {p.get('title')!r}{'' if ok else '   <-- MISSING'}")
    print(f"\n{'OK: all panes have cwd + tty_name' if not missing else f'{missing} pane(s) missing cwd or tty_name'}")


if __name__ == "__main__":
    main()
