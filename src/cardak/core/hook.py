"""`cardak hook`: record one Claude Code hook event and ping if a session started waiting.

Claude Code runs this on every hook, synchronously. It must stay fast, print nothing and
always exit 0: stdout can reach the conversation, and exit code 2 blocks a tool call. So
it imports no Textual or psutil, and any failure goes to hook.log instead.
"""

import os
import time
import traceback
from collections.abc import Mapping
from pathlib import Path
from typing import TextIO

from cardak.collectors.hooks import parse
from cardak.core.agents import apply
from cardak.core.alerts import Spawn, alert, spawn_detached
from cardak.core.config import Config, ConfigError, config_path, load, state_dir
from cardak.core.focus import focus_path, focused_pane, read_history
from cardak.core.store import Store, db_path

LOG_MAX = 1_000_000
MAX_PAYLOAD = 20_000  # a tool's output can be huge; the event log keeps the start


def project_name(cwd: Path | None, config: Config) -> str:
    if cwd is None:
        return "claude"
    matches = [p for p in config.projects if cwd.is_relative_to(p.path)]
    if matches:
        return max(matches, key=lambda p: len(p.path.parts)).name
    return cwd.name or "claude"


def _pane(env: Mapping[str, str]) -> int | None:
    pane = env.get("WEZTERM_PANE", "")
    return int(pane) if pane.isdigit() else None


def run(
    text: str,
    env: Mapping[str, str],
    now: float,
    *,
    store: Store,
    config_file: Path,
    focus_file: Path,
    spawn: Spawn = spawn_detached,
) -> None:
    event = parse(text)
    if event is None:
        return
    pane = _pane(env)
    store.record_event(event.session_id, event.name, text[:MAX_PAYLOAD], now)
    ping = store.update(event.session_id, lambda old: apply(old, event, pane, now))
    if event.name == "SessionStart":
        store.prune(now)
    if ping is None:
        return
    if pane is not None and focused_pane(read_history(focus_file)) == pane:
        return  # the user is looking at it already
    try:
        config = load(config_file)
    except ConfigError:
        config = Config()
    alert(project_name(ping.cwd, config), ping, config.notify, spawn=spawn)


def _log(env: Mapping[str, str], message: str) -> None:
    log = state_dir(env) / "hook.log"
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        mode = "w" if log.exists() and log.stat().st_size > LOG_MAX else "a"
        with log.open(mode, encoding="utf-8") as f:
            f.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')}\n{message}\n")
    except OSError:
        pass  # nowhere left to report it; never fail the hook


def main(stdin: TextIO, env: Mapping[str, str] | None = None) -> int:
    env = os.environ if env is None else env
    try:
        text = stdin.read()
        store = Store(db_path(env))
        try:
            run(
                text,
                env,
                time.time(),
                store=store,
                config_file=config_path(None, env),
                focus_file=focus_path(env),
            )
        finally:
            store.close()
    except Exception:
        _log(env, traceback.format_exc())
    return 0
