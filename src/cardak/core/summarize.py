"""`cardak summarize <session_id> <wait_since>`: write one waiting session's summary.

`cardak hook` starts this detached when a session starts waiting, so it may take seconds and
import anything. Like the hook it prints nothing and always exits 0; failures go to hook.log.
"""

import json
import os
import time
import traceback
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from cardak.collectors.hooks import parse
from cardak.collectors.transcripts import read_transcript
from cardak.core.agents import SessionState
from cardak.core.config import Config, ConfigError, anthropic_key_path, config_path, load
from cardak.core.hook import log_problem, project_name
from cardak.core.store import Store, SummaryRow, db_path
from cardak.core.summary import (
    SYSTEM_PROMPT,
    KeyProblem,
    Summarizer,
    build_input,
    cost,
    month_start,
    read_key,
    turn_text,
    validate,
)

MakeSummarizer = Callable[[str], Summarizer]


def _inputs(
    store: Store, state: SessionState, wait_since: float
) -> tuple[str, tuple[str, str] | None]:
    """What Claude said this turn and the pending tool, from the current wait only."""
    event = store.event_at(state.session_id, wait_since)
    if event is not None and event[0] == "Stop":
        return state.last_message or "", None  # that Stop just set last_message
    # mid-turn: last_message is still the previous turn's, so read this turn's text instead
    text = ""
    if state.transcript_path is not None and state.transcript_path.exists():
        text = turn_text(read_transcript(state.transcript_path)[0])
    parsed = parse(event[1]) if event is not None else None
    if parsed is not None and parsed.tool_name:
        return text, (parsed.tool_name, json.dumps(parsed.tool_input))
    return text, (state.current_tool or "tool", state.reason or "")  # payload cut or missing


def run(
    session_id: str,
    wait_since: float,
    now: float,
    *,
    store: Store,
    config: Config,
    key_file: Path,
    make_summarizer: MakeSummarizer,
) -> None:
    if not config.summary.enabled:
        return
    state = store.get(session_id)
    if state is None or state.status != "waiting" or state.status_since != wait_since:
        return  # answered already, or a newer wait
    if store.has_summary(session_id, wait_since):
        return
    try:
        key = read_key(key_file)
    except KeyProblem:
        return
    if store.spend_since(month_start(now)) >= config.summary.monthly_limit_usd:
        return
    assistant_text, tool = _inputs(store, state, wait_since)
    user = build_input(
        project_name(state.cwd, config), state, assistant_text=assistant_text, tool=tool
    )
    reply = make_summarizer(key).summarize(SYSTEM_PROMPT, user)
    store.add_summary(
        SummaryRow(
            session_id=session_id,
            wait_since=wait_since,
            text=validate(reply),  # None still records the cost, and the wait isn't retried
            model=reply.model,
            input_tokens=reply.input_tokens,
            output_tokens=reply.output_tokens,
            cost_usd=cost(reply.model, reply.input_tokens, reply.output_tokens),
            created_at=now,
        )
    )


def main(
    args: Sequence[str], make_summarizer: MakeSummarizer, env: Mapping[str, str] | None = None
) -> int:
    env = os.environ if env is None else env
    try:
        session_id, wait_since = args[0], float(args[1])
        try:
            config = load(config_path(None, env))
        except ConfigError:
            config = Config()
        store = Store(db_path(env))
        try:
            run(
                session_id,
                wait_since,
                time.time(),
                store=store,
                config=config,
                key_file=anthropic_key_path(env),
                make_summarizer=make_summarizer,
            )
        finally:
            store.close()
    except Exception:
        log_problem(env, "summarize: " + traceback.format_exc())
    return 0
