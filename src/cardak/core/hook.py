"""`cardak hook`: record one Claude Code hook event and ping if a session started waiting.

Claude Code runs this on every hook, synchronously. It must stay fast, print nothing and
always exit 0: stdout can reach the conversation, and exit code 2 blocks a tool call. So
it imports no Textual or psutil, and any failure goes to hook.log instead.
"""

from __future__ import annotations

import os
import sys
import time
import traceback
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

from cardak.collectors.hooks import HookEvent, parse
from cardak.core.agents import HANDLERS, IGNORED_EVENTS, Ping, apply
from cardak.core.config import (
    Config,
    ConfigError,
    anthropic_key_path,
    config_path,
    load,
    real_path,
)
from cardak.core.focus import focus_path, focused_pane, read_history
from cardak.core.hooklog import log_problem
from cardak.core.store import Store, db_path

if TYPE_CHECKING:
    from cardak.core.alerts import Spawn

MAX_PAYLOAD = 20_000  # a tool's output can be huge; the event log keeps the start


def project_name(cwd: Path | None, config: Config) -> str:
    if cwd is None:
        return "claude"
    # a project and its real path, against the cwd and its real path: as the deck groups (#14)
    roots = [(p, root) for p in config.projects for root in {p.path, real_path(p.path)}]
    matches = [(p, root) for p, root in roots if cwd.is_relative_to(root)]
    if not matches:
        real = real_path(cwd)
        matches = [(p, root) for p, root in roots if real.is_relative_to(root)]
    if matches:
        return max(matches, key=lambda m: len(m[1].parts))[0].name
    return cwd.name or "claude"


def _pane(env: Mapping[str, str]) -> int | None:
    pane = env.get("WEZTERM_PANE", "")
    return int(pane) if pane.isdigit() else None


def summarize_argv(session_id: str, wait_since: float) -> list[str]:
    """`cardak summarize` under this interpreter: no new module at the package root needed."""
    script = "import sys; from cardak.cli import main; sys.exit(main())"
    # -P: never put the cwd (the Claude session's project) on sys.path
    return [sys.executable, "-P", "-c", script, "summarize", session_id, repr(wait_since)]


def run(
    text: str,
    env: Mapping[str, str],
    now: float,
    *,
    store: Store,
    config_file: Path,
    focus_file: Path,
    key_file: Path,
    spawn: Spawn | None = None,
) -> None:
    event = parse(text)
    if event is None:
        return
    _record(event, text, env, now, store=store, config_file=config_file, focus_file=focus_file,
            key_file=key_file, spawn=spawn)  # fmt: skip


def _record(
    event: HookEvent,
    text: str,
    env: Mapping[str, str],
    now: float,
    *,
    store: Store,
    config_file: Path,
    focus_file: Path,
    key_file: Path,
    spawn: Spawn | None = None,
) -> None:
    if event.name not in HANDLERS and event.name not in IGNORED_EVENTS:
        log_problem(env, f"ignored: unknown event {event.name}")  # Claude Code added one
    pane = _pane(env)
    store.record_event(event.session_id, event.name, text[:MAX_PAYLOAD], now)
    ping = store.update(event.session_id, lambda old: apply(old, event, pane, now))
    if event.name == "SessionStart":
        store.prune(now)
    if ping is None:
        return
    if pane is not None and focused_pane(read_history(focus_file)) == pane:
        return  # the user is looking at it already
    _ping(ping, now, config_file=config_file, key_file=key_file, spawn=spawn)


def _ping(
    ping: Ping, now: float, *, config_file: Path, key_file: Path, spawn: Spawn | None
) -> None:
    """A session started waiting: notification, sound, and a summary when that's on."""
    try:
        config = load(config_file)
    except ConfigError:
        config = Config()
    # imported only when pinging: alerts reads the sound pack
    from cardak.core.alerts import alert, spawn_detached

    spawn = spawn or spawn_detached
    if not ping.quiet:
        alert(project_name(ping.cwd, config), ping, config.notify, spawn=spawn)
    if config.summary.enabled and key_file.exists():
        spawn(summarize_argv(ping.session_id, now))  # `now` is the new wait's status_since


def main(stdin: TextIO, env: Mapping[str, str] | None = None) -> int:
    env = os.environ if env is None else env
    try:
        text = stdin.read()
        event = parse(text)
        if event is None:  # before opening the store: garbage doesn't create the database
            log_problem(env, f"ignored: unparseable payload ({len(text)} bytes): {text[:200]!r}")
            return 0
        store = Store(db_path(env))
        try:
            _record(
                event,
                text,
                env,
                time.time(),
                store=store,
                config_file=config_path(None, env),
                focus_file=focus_path(env),
                key_file=anthropic_key_path(env),
            )
        finally:
            store.close()
    except Exception:
        log_problem(env, traceback.format_exc())
    return 0
