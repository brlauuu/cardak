# Waiting summaries Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Each waiting Claude session's line in "Waiting on you" shows a deterministic tag (`approve` / `decide` / `review`) and a one-line Haiku summary of what it did and what it needs.

**Architecture:** `cardak hook` spawns a detached `cardak summarize <session_id> <wait_since>` when a ping fires. That job (`core/summarize.py`) builds an input from `cardak.db` and the current turn's transcript, calls a `Summarizer` (the Anthropic one lives in the new `llm/` package, wired by `cli.py`), validates the line and stores it with its cost in a new `summaries` table. The deck reads summaries for the current waits on each poll.

**Tech Stack:** Python 3.13, uv, Textual/rich, SQLite, the `anthropic` Python SDK (new), pytest.

**Spec:** `docs/superpowers/specs/2026-10-21-waiting-summaries-design.md`

## Global Constraints

- Model `claude-haiku-5-5`; prices $0.10 / $0.50 per million input / output tokens.
- Request: `max_tokens=256`, `thinking={"type": "disabled"}`, `output_config={"effort": "low"}`; client `timeout=20.0`, `max_retries=2`, `api_key=` passed explicitly.
- Key file `~/.config/cardak/anthropic_key` (`config_dir() / "anthropic_key"`); refused when `mode & 0o077`; `ANTHROPIC_API_KEY` ignored.
- `[summary] monthly_limit_usd`, default `5.0`, `0` disables; negative or non-number is a `ConfigError`.
- Line: requested ≤ 80 chars; accepted ≤ 120 chars, one line, non-empty; `stop_reason` must be `end_turn`.
- Inputs cut: last prompt 2 000 chars (start kept), assistant text 4 000 chars (**end** kept), tool input JSON 2 000 chars, at most 10 `turn_files`.
- Summaries pruned after 62 days; spend counted per local calendar month.
- `cardak hook` and `cardak summarize` print nothing and exit 0; failures go to `hook.log`.
- `cardak hook` must not import `anthropic`, `cardak.llm`, `cardak.core.summary` or `cardak.core.summarize` (extend `test_hook_imports_only_what_it_needs`).
- Layering: `cli → {ui, wezterm, claude, llm} → core → collectors`; `llm/` imports nothing from cardak; no new module at the package root.
- No test touches the network. Coverage floor 80 % branch, don't lower it.
- Copy (exact):
  - key warning: `anthropic_key is readable by other users; run chmod 600 ~/.config/cardak/anthropic_key`
  - cap notice: `Summary limit of $5.00 reached for this month; showing raw reasons. Change it in [summary] in projects.toml.` (amount formatted from the setting)
  - detail spend line: `Summaries this month: $0.42 of $5.00`

## Review Focus

1. **A wait's event payload was truncated** at `MAX_PAYLOAD` (20 000 chars) and is no longer valid JSON: the job must still summarize, using `state.reason` as the tool description. Test in Task 5.
2. **`wait_since` round-trips through argv**: the float written by the hook must compare equal to `status_since` read back from SQLite. Test in Task 6.
3. **The API call raises** (network down, 401, timeout): nothing is printed, no row is written, the traceback lands in `hook.log`, and the deck keeps the raw reason. Test in Task 5.
4. **Key file with a trailing newline, CRLF, or only whitespace**: the first two work, the third counts as missing. Test in Task 3.
5. **Month boundary**: a summary created at 23:59 on the last day of the previous month (local time) does not count toward this month's spend. Test in Task 2.

---

### Task 1: `[summary]` config and the key path

**Files:**
- Modify: `src/cardak/core/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `SummarySettings(monthly_limit_usd: float = 5.0)` with property `enabled -> bool` (`monthly_limit_usd > 0`); `Config.summary: SummarySettings = SummarySettings()`; `anthropic_key_path(env: Mapping[str, str] | None = None) -> Path` (= `config_dir(env) / "anthropic_key"`).

- [ ] **Step 1: Write the failing tests**

```python
def test_summary_defaults(tmp_path: Path) -> None:
    cfg = load(write(tmp_path, ""))
    assert cfg.summary.monthly_limit_usd == 5.0 and cfg.summary.enabled

def test_summary_limit_zero_disables(tmp_path: Path) -> None:
    assert not load(write(tmp_path, "[summary]\nmonthly_limit_usd = 0\n")).summary.enabled

def test_summary_limit_accepts_int_and_float(tmp_path: Path) -> None:
    assert load(write(tmp_path, "[summary]\nmonthly_limit_usd = 2\n")).summary.monthly_limit_usd == 2.0

@pytest.mark.parametrize("body", ["[summary]\nmonthly_limit_usd = -1\n",
                                  '[summary]\nmonthly_limit_usd = "5"\n',
                                  "[summary]\nmonthly_limit_usd = true\n",
                                  "summary = 3\n"])
def test_summary_rejects_bad_values(tmp_path: Path, body: str) -> None:
    with pytest.raises(ConfigError, match=r"\[summary\]"):
        load(write(tmp_path, body))

def test_anthropic_key_path() -> None:
    assert anthropic_key_path({"XDG_CONFIG_HOME": "/x"}) == Path("/x/cardak/anthropic_key")
```

(`write` is whatever helper `tests/test_config.py` already uses to write a projects.toml; reuse it.)

- [ ] **Step 2: Run** `uv run pytest tests/test_config.py -q` — Expected: FAIL (ImportError / AttributeError).

- [ ] **Step 3: Implement** `SummarySettings`, `_summary(path, data) -> SummarySettings` (mirrors `_notify`; reject `bool` before the `int | float` check; messages `[summary] must be a table`, `[summary] monthly_limit_usd must be a number ≥ 0`), `Config.summary`, `anthropic_key_path`.

- [ ] **Step 4: Run** `uv run pytest tests/test_config.py -q` — Expected: PASS.

- [ ] **Step 5: Commit** `feat(core): [summary] settings and the key path (#30)`

---

### Task 2: Store schema v2 — `summaries`

**Files:**
- Modify: `src/cardak/core/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Produces, in `core/store.py`:
  - `SCHEMA_VERSION = 2`; `SUMMARY_KEEP_SECONDS = 62 * 86_400`
  - `@dataclass(frozen=True) class SummaryRow: session_id: str; wait_since: float; text: str | None; model: str; input_tokens: int; output_tokens: int; cost_usd: float; created_at: float`
  - `Store.add_summary(row: SummaryRow) -> None` (`INSERT OR IGNORE`)
  - `Store.has_summary(session_id: str, wait_since: float) -> bool`
  - `Store.summaries_for(waits: Iterable[tuple[str, float]]) -> dict[str, str]` — session_id → text, only rows with text
  - `Store.spend_since(ts: float) -> float` — `SUM(cost_usd)` of rows with `created_at >= ts`, `0.0` when none
  - `Store.event_at(session_id: str, ts: float) -> tuple[str, str] | None` — `(event, payload_json)` of the event recorded at exactly `ts`
  - `prune` also deletes summaries with `created_at < now - SUMMARY_KEEP_SECONDS`
- All new methods wrap `sqlite3.Error` in `StoreError`, like the existing ones.

- [ ] **Step 1: Write the failing tests**

```python
def row(sid="s", since=10.0, text="did x; needs y", cost=0.001, at=100.0) -> SummaryRow:
    return SummaryRow(sid, since, text, "claude-haiku-5-5", 1500, 30, cost, at)

def test_summary_roundtrip(tmp_path: Path) -> None:
    store = Store(tmp_path / "db")
    store.add_summary(row())
    store.add_summary(row(sid="t", text=None))
    assert store.has_summary("s", 10.0) and store.has_summary("t", 10.0)
    assert not store.has_summary("s", 11.0)
    assert store.summaries_for([("s", 10.0), ("t", 10.0), ("s", 11.0)]) == {"s": "did x; needs y"}

def test_add_summary_ignores_a_second_row_for_the_same_wait(tmp_path: Path) -> None:
    store = Store(tmp_path / "db")
    store.add_summary(row(text="first"))
    store.add_summary(row(text="second"))
    assert store.summaries_for([("s", 10.0)]) == {"s": "first"}

def test_spend_since(tmp_path: Path) -> None:
    store = Store(tmp_path / "db")
    assert store.spend_since(0.0) == 0.0
    store.add_summary(row(sid="a", cost=0.25, at=50.0))
    store.add_summary(row(sid="b", cost=0.5, at=150.0))
    assert store.spend_since(100.0) == 0.5

def test_event_at(tmp_path: Path) -> None:
    store = Store(tmp_path / "db")
    store.record_event("s", "PermissionRequest", '{"x": 1}', 12.5)
    assert store.event_at("s", 12.5) == ("PermissionRequest", '{"x": 1}')
    assert store.event_at("s", 12.6) is None

def test_prune_keeps_summaries_62_days(tmp_path: Path) -> None:
    store = Store(tmp_path / "db")
    day = 86_400.0
    store.add_summary(row(sid="old", at=0.0))
    store.add_summary(row(sid="new", at=10 * day))
    store.prune(62 * day + 1)
    assert not store.has_summary("old", 10.0) and store.has_summary("new", 10.0)

def test_v1_database_gains_summaries(tmp_path: Path) -> None:
    path = tmp_path / "db"
    con = sqlite3.connect(path)
    con.executescript(V1_SCHEMA)  # agent_sessions + events as in 0.4.0, copied into the test
    con.execute("PRAGMA user_version = 1"); con.commit(); con.close()
    store = Store(path)
    store.add_summary(row())
    assert store.has_summary("s", 10.0)
```

Review Focus 5 (month boundary) is tested in Task 3 against `month_start`; here `spend_since` just takes the timestamp.

- [ ] **Step 2: Run** `uv run pytest tests/test_store.py -q` — Expected: FAIL.

- [ ] **Step 3: Implement** the table and index exactly as in the spec ("Store"), appended to `_SCHEMA`; bump `SCHEMA_VERSION`; the methods above. `summaries_for` with an empty iterable returns `{}` without querying.

- [ ] **Step 4: Run** `uv run pytest tests/test_store.py -q` — Expected: PASS.

- [ ] **Step 5: Commit** `feat(core): summaries table, store schema v2 (#30)`

---

### Task 3: `core/summary.py` — the pure parts

**Files:**
- Create: `src/cardak/core/summary.py`
- Create: `tests/fixtures/summaries/` (three small transcript JSONL files: `mid-turn.jsonl` with an earlier turn, a user prompt, assistant text, a tool_use; `no-assistant-text.jsonl`; reuse `tests/fixtures/transcripts/basic.jsonl` where it fits)
- Test: `tests/test_summary.py`

**Interfaces:**
- Consumes: `SessionState`, `WaitKind` (`core/agents.py`); `Entry`, `read_transcript` (`collectors/transcripts.py`); `SummarySettings` (Task 1).
- Produces:
  - `MODEL = "claude-haiku-5-5"`; `PRICES: dict[str, tuple[float, float]] = {"claude-haiku-5-5": (0.10, 0.50)}` (USD per million in/out)
  - `TAGS: dict[WaitKind, str] = {"permission": "approve", "question": "decide", "finished": "review"}`
  - `SYSTEM_PROMPT: str` — the rules in the spec's "Prompt and validation", worded by the implementer
  - `class Reply(Protocol)`: attributes `text: str | None`, `model: str`, `stop_reason: str | None`, `input_tokens: int`, `output_tokens: int`
  - `class Summarizer(Protocol)`: `def summarize(self, system: str, user: str) -> Reply: ...`
  - `@dataclass(frozen=True) class SummaryInfo: text: str | None; month_spend: float; limit: float`
  - `class KeyProblem(Exception)`; `read_key(path: Path) -> str` — raises `KeyProblem("missing")` when absent or blank, `KeyProblem("too open")` when `stat().st_mode & 0o077`; returns the stripped key
  - `key_warning(path: Path) -> str | None` — the exact key-warning copy when the file exists and is too open, else `None`
  - `cost(model: str, input_tokens: int, output_tokens: int) -> float`
  - `month_start(now: float) -> float` — local midnight on the 1st of `now`'s month
  - `turn_text(entries: Sequence[Entry]) -> str` — assistant text entries after the last `user` entry, joined with blank lines; `""` when none
  - `build_input(project: str, state: SessionState, *, assistant_text: str, tool: tuple[str, str] | None) -> str` — `tool` is `(name, input_json)`
  - `validate(reply: Reply) -> str | None`
  - `cut_start(text: str, limit: int) -> str`, `cut_end(text: str, limit: int) -> str` (helpers, `…` marks the cut)

- [ ] **Step 1: Write the failing tests**

```python
def test_tags_cover_every_wait_kind() -> None:
    assert TAGS == {"permission": "approve", "question": "decide", "finished": "review"}

def test_cost() -> None:
    assert cost("claude-haiku-5-5", 1_000_000, 1_000_000) == pytest.approx(0.60)
    assert cost("claude-haiku-5-5", 1500, 30) == pytest.approx(0.000165)

def test_month_start_is_local_midnight_on_the_first() -> None:
    now = datetime(2026, 11, 1, 0, 0, 30).timestamp()
    assert month_start(now) == datetime(2026, 11, 1).timestamp()
    assert datetime(2026, 10, 31, 23, 59).timestamp() < month_start(now)   # Review Focus 5

@pytest.mark.parametrize("raw", ["sk-ant-x\n", "sk-ant-x\r\n", "  sk-ant-x  "])
def test_read_key_strips(tmp_path: Path, raw: str) -> None:
    key = tmp_path / "anthropic_key"; key.write_text(raw); key.chmod(0o600)
    assert read_key(key) == "sk-ant-x"

def test_read_key_blank_or_absent_is_missing(tmp_path: Path) -> None:   # Review Focus 4
    key = tmp_path / "anthropic_key"
    with pytest.raises(KeyProblem, match="missing"): read_key(key)
    key.write_text(" \n"); key.chmod(0o600)
    with pytest.raises(KeyProblem, match="missing"): read_key(key)

def test_read_key_too_open(tmp_path: Path) -> None:
    key = tmp_path / "anthropic_key"; key.write_text("k"); key.chmod(0o640)
    with pytest.raises(KeyProblem, match="too open"): read_key(key)
    assert key_warning(key) == ("anthropic_key is readable by other users; "
                                "run chmod 600 ~/.config/cardak/anthropic_key")
    key.chmod(0o600)
    assert key_warning(key) is None and key_warning(tmp_path / "absent") is None

def test_turn_text_is_only_the_current_turn() -> None:
    entries, _ = read_transcript(FIXTURES / "summaries" / "mid-turn.jsonl")
    text = turn_text(entries)
    assert "CURRENT TURN TEXT" in text and "PREVIOUS TURN TEXT" not in text

def test_turn_text_empty_without_assistant_text() -> None:
    entries, _ = read_transcript(FIXTURES / "summaries" / "no-assistant-text.jsonl")
    assert turn_text(entries) == ""

def test_build_input_contains_only_given_fields() -> None:
    state = SessionState("s", kind="permission", last_prompt="p" * 3000,
                         last_message="OLD TURN", turn_edits=2, turn_files=tuple(f"f{i}" for i in range(12)))
    text = build_input("api-gw", state, assistant_text="x" * 5000 + "THE ASK",
                       tool=("Bash", '{"command": "cargo test"}'))
    assert "OLD TURN" not in text                      # the caller decides what assistant text is
    assert "THE ASK" in text and "p" * 2001 not in text
    assert "cargo test" in text and "f9" in text and "f10" not in text
    assert "api-gw" in text

def test_build_input_without_tool() -> None:
    text = build_input("notes", SessionState("s", kind="finished"), assistant_text="done", tool=None)
    assert "done" in text

@dataclass
class R:
    text: str | None; stop_reason: str | None = "end_turn"
    model: str = "claude-haiku-5-5"; input_tokens: int = 1; output_tokens: int = 1

@pytest.mark.parametrize("reply, expected", [
    (R("  Fixed it; wants tests  "), "Fixed it; wants tests"),
    (R('"Fixed it; wants tests"'), "Fixed it; wants tests"),
    (R("Fixed   it;\twants"), "Fixed it; wants"),
    (R(""), None), (R(None), None), (R("a\nb"), None), (R("x" * 121), None),
    (R("x" * 120), "x" * 120),
    (R("ok", stop_reason="max_tokens"), None), (R("ok", stop_reason="refusal"), None),
])
def test_validate(reply: R, expected: str | None) -> None:
    assert validate(reply) == expected
```

- [ ] **Step 2: Run** `uv run pytest tests/test_summary.py -q` — Expected: FAIL (module missing).

- [ ] **Step 3: Implement** `core/summary.py` with the interfaces above. `build_input` lays the fields out as labelled, delimited sections (implementer's format), with prompt `cut_start(…, 2000)`, assistant text `cut_end(…, 4000)`, tool input `cut_start(…, 2000)`, counts, `turn_files[:10]`. `validate` strips whitespace, then one pair of surrounding `"`/`'`, collapses runs of spaces/tabs, rejects a remaining newline.

- [ ] **Step 4: Run** `uv run pytest tests/test_summary.py -q` — Expected: PASS.

- [ ] **Step 5: Commit** `feat(core): summary input, validation, cost and key (#30)`

---

### Task 4: `llm/anthropic.py` — the Anthropic provider

**Files:**
- Modify: `pyproject.toml`, `uv.lock` (`uv add anthropic`)
- Create: `src/cardak/llm/__init__.py` (docstring only), `src/cardak/llm/anthropic.py`
- Test: `tests/test_llm_anthropic.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) class Reply: text: str | None; model: str; stop_reason: str | None; input_tokens: int; output_tokens: int` (structurally satisfies `core.summary.Reply`); `class AnthropicSummarizer: __init__(self, api_key: str, model: str, client: object | None = None)`; `summarize(self, system: str, user: str) -> Reply`. Imports nothing from cardak.

- [ ] **Step 1: Add the dependency and check the SDK.** Run `uv add anthropic`. PyPI's current major is **2.x**, while the API reference skill documents 1.x: before writing code, read the installed package (`uv run python -c "import anthropic, inspect; print(anthropic.__version__); print(inspect.signature(anthropic.Anthropic().messages.create))"` with a dummy `api_key`) and its CHANGELOG for 2.0, and confirm `api_key`, `timeout`, `max_retries` on the client and `model`, `max_tokens`, `system`, `messages`, `thinking`, `output_config` on `messages.create`, plus `response.content[*].type/.text`, `response.stop_reason`, `response.usage.input_tokens/.output_tokens`. If a name differs, use the 2.x name and say so in the PR.

- [ ] **Step 2: Write the failing tests** (fake client: an object with `.messages.create(**kw)` recording `kw` and returning a `SimpleNamespace` response)

```python
def test_request_parameters() -> None:
    fake = FakeClient(response(text="Did x; needs y"))
    AnthropicSummarizer("k", "claude-haiku-5-5", client=fake).summarize("SYS", "USER")
    kw = fake.calls[0]
    assert kw["model"] == "claude-haiku-5-5" and kw["max_tokens"] == 256
    assert kw["thinking"] == {"type": "disabled"} and kw["output_config"] == {"effort": "low"}
    assert kw["system"] == "SYS" and kw["messages"] == [{"role": "user", "content": "USER"}]

def test_reply_fields() -> None:
    reply = AnthropicSummarizer("k", "claude-haiku-5-5", client=FakeClient(
        response(text="Did x", stop_reason="end_turn", usage=(1500, 30)))).summarize("s", "u")
    assert reply == Reply("Did x", "claude-haiku-5-5", "end_turn", 1500, 30)

def test_first_text_block_wins_and_no_text_is_none() -> None:
    blocks = [SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text="A"),
              SimpleNamespace(type="text", text="B")]
    assert summarize_with(blocks).text == "A"
    assert summarize_with([]).text is None

def test_real_client_gets_the_key_explicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-env")
    s = AnthropicSummarizer("from-file", "claude-haiku-5-5")
    assert s.client.api_key == "from-file"          # attribute name per the installed SDK
    assert s.client.max_retries == 2               # and timeout 20.0, per the SDK's attribute
```

- [ ] **Step 3: Run** `uv run pytest tests/test_llm_anthropic.py -q` — Expected: FAIL.

- [ ] **Step 4: Implement** `AnthropicSummarizer`: when `client` is `None`, `import anthropic` inside `__init__` and build `anthropic.Anthropic(api_key=api_key, timeout=20.0, max_retries=2)`; `summarize` calls `messages.create` with the Global Constraints parameters and maps the response to `Reply`. Exceptions propagate (the job logs them).

- [ ] **Step 5: Run** `uv run pytest tests/test_llm_anthropic.py -q && uv run pyright` — Expected: PASS, 0 errors.

- [ ] **Step 6: Commit** `feat(llm): Anthropic summarizer (#30)`

---

### Task 5: `cardak summarize` — the background job

**Files:**
- Create: `src/cardak/core/summarize.py`
- Modify: `src/cardak/core/hook.py` (rename `_log` → `log_problem(env, message)`, callers updated), `src/cardak/cli.py` (subcommand)
- Test: `tests/test_summarize.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: Tasks 1–3 (`load`, `anthropic_key_path`, `Store` methods, `SummaryRow`, everything in `core/summary.py`), `collectors.hooks.parse`, `read_transcript`, `hook.project_name`, `hook.log_problem`.
- Produces:
  - `core/summarize.py: run(session_id: str, wait_since: float, now: float, *, store: Store, config: Config, key_file: Path, make_summarizer: Callable[[str], Summarizer]) -> None` — `make_summarizer(api_key)`; returns silently on every "stop" condition in the spec's step list
  - `core/summarize.py: main(args: Sequence[str], make_summarizer: Callable[[str], Summarizer], env: Mapping[str, str] | None = None) -> int` — `args` is `[session_id, wait_since]`; opens the store at `db_path(env)`, loads `config_path(None, env)` (a `ConfigError` → `Config()`), always returns 0, logs any exception with `log_problem(env, "summarize: " + traceback)`
  - `cli.py`: subcommand `summarize SESSION_ID WAIT_SINCE` (help `summarize a waiting session (run by cardak hook)`), which calls `summarize.main([sid, since], lambda key: AnthropicSummarizer(key, MODEL))`

Job logic for the inputs (the spec's "What is sent"): `event = store.event_at(sid, wait_since)`; `from_stop = event is not None and event[0] == "Stop"`; assistant text is `state.last_message or ""` when `from_stop`, else `turn_text(read_transcript(state.transcript_path)[0])` when that path exists, else `""`; tool is `None` when `from_stop`, else `(parsed.tool_name, json.dumps(parsed.tool_input))` from `parse(event[1])`, falling back to `(state.current_tool or "tool", state.reason or "")` when there is no event or it doesn't parse. The row stores `validate(reply)` (possibly `None`) and `cost(reply.model, …)`.

- [ ] **Step 1: Write the failing tests** (stub summarizer recording `(system, user)` and returning a configurable `Reply`; a store seeded by running `hook.run` with fixtures from `tests/hookfix.py`, so states and events are real)

```python
def test_writes_a_summary_for_a_finished_turn(...) -> None:
    sid, since = seed(store, "stop-finished")
    run(sid, since, since + 1, store=store, config=Config(), key_file=key, make_summarizer=stub)
    assert store.summaries_for([(sid, since)]) == {sid: "Did x; nothing asked"}
    assert stub.calls and SYSTEM_PROMPT == stub.calls[0][0]

def test_stop_wait_sends_last_message_and_no_tool(...) -> None: ...      # user text contains the Stop message, no "tool" section
def test_mid_turn_wait_never_sends_previous_last_message(...) -> None:
    # seed: Stop("PREVIOUS TURN TEXT") → UserPromptSubmit → PreToolUse → PermissionRequest,
    # transcript_path pointing at fixtures/summaries/mid-turn.jsonl
    assert "PREVIOUS TURN TEXT" not in user and "CURRENT TURN TEXT" in user and "cargo test" in user
def test_truncated_event_payload_falls_back_to_reason(...) -> None:     # Review Focus 1
    # overwrite the event's payload_json with its first 50 chars; summary still written,
    # user text contains state.reason
def test_skips_when_the_wait_changed(...) -> None:    # run with wait_since + 1 → no call, no row
def test_skips_when_already_summarized(...) -> None:  # second run → one call total
def test_skips_when_disabled(...) -> None:            # Config(summary=SummarySettings(0)) → no call
def test_skips_without_key_or_with_open_key(...) -> None:  # absent; chmod 0o644 → no call
def test_skips_over_the_cap(...) -> None:
    store.add_summary(SummaryRow("other", 1.0, "x", MODEL, 0, 0, 5.0, now))
    # limit 5.0 → no call
def test_rejected_output_is_recorded_without_text(...) -> None:
    # stub returns R("a\nb") → has_summary true, summaries_for empty, spend > 0
def test_main_logs_api_errors_silently(tmp_path, capsys) -> None:        # Review Focus 3
    # make_summarizer returns an object whose summarize raises RuntimeError("boom")
    assert main([sid, repr(since)], boom, env) == 0
    assert capsys.readouterr() == ("", "")
    assert "summarize:" in log.read_text() and "boom" in log.read_text()
    assert not store.has_summary(sid, since)
def test_cli_summarize_dispatches(monkeypatch) -> None:   # tests/test_cli.py: main(["summarize", "s", "1.5"]) calls summarize.main with ["s", "1.5"]
```

- [ ] **Step 2: Run** `uv run pytest tests/test_summarize.py tests/test_cli.py -q` — Expected: FAIL.

- [ ] **Step 3: Implement** `core/summarize.py`, the `log_problem` rename, and the CLI subcommand (imports `cardak.core.summarize` and `cardak.llm.anthropic` inside the dispatch branch only).

- [ ] **Step 4: Run** `uv run pytest -q` — Expected: PASS (whole suite, since `_log` was renamed).

- [ ] **Step 5: Commit** `feat: cardak summarize writes a waiting session's summary (#30)`

---

### Task 6: The hook spawns the summarizer

**Files:**
- Modify: `src/cardak/core/hook.py`
- Test: `tests/test_hook.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `Config.summary.enabled`, `anthropic_key_path` (Task 1); the `summarize` CLI (Task 5).
- Produces: `hook.summarize_argv(session_id: str, wait_since: float) -> list[str]` = `[sys.executable, "-c", "import sys; from cardak.cli import main; sys.exit(main())", "summarize", session_id, repr(wait_since)]`. `run` gains keyword `key_file: Path` (callers: `main` passes `anthropic_key_path(env)`; tests pass a temp path).

In `run`, after `alert(...)`: if `config.summary.enabled and key_file.exists()`, `spawn(summarize_argv(ping.session_id, now))` (`now` is the wait's `status_since`: `_set_status` sets it to `now` on the transition that produced the ping).

- [ ] **Step 1: Write the failing tests**

The `paths` fixture in `tests/test_hook.py` gains `"key_file": tmp_path / "anthropic_key"` (absent by default), so every existing `call(...)` passes it through to `run`.

```python
def test_ping_spawns_the_summarizer(tmp_path, paths) -> None:
    paths["key_file"].write_text("k")
    spawned = call(store, paths, "stop-finished")
    argv = spawned[-1]
    assert argv[:2] == [sys.executable, "-c"] and argv[3:5] == ["summarize", state.session_id]
    assert float(argv[5]) == store.live_sessions()[0].status_since      # Review Focus 2

def test_no_summarizer_without_key(...) -> None:        # key_file absent → only notify-send
def test_no_summarizer_when_disabled(...) -> None:      # [summary] monthly_limit_usd = 0
def test_no_summarizer_when_pane_focused(...) -> None:  # focus file on pane 7 → nothing spawned
def test_summarize_argv_runs_the_cli(tmp_path) -> None:
    # subprocess.run(summarize_argv("nope", 1.0), env with XDG_STATE_HOME/CARDAK_CONFIG temp) →
    # returncode 0, empty stdout/stderr (no such session: the job stops silently)
```

Extend `tests/test_cli.py::test_hook_imports_only_what_it_needs`'s module list with `'anthropic', 'cardak.llm', 'cardak.core.summary', 'cardak.core.summarize'`, and add a variant of it that runs a `stop-finished` payload with a key file present (so the ping path runs) and asserts the same list.

- [ ] **Step 2: Run** `uv run pytest tests/test_hook.py tests/test_cli.py -q` — Expected: FAIL.

- [ ] **Step 3: Implement** `summarize_argv`, the `key_file` parameter and the spawn.

- [ ] **Step 4: Run** `uv run pytest -q` — Expected: PASS. Then measure: `python -X importtime -c "import cardak.cli" 2>&1 | tail -3` shows no new heavy imports.

- [ ] **Step 5: Commit** `feat(core): the hook starts a summary when a session starts waiting (#30)`

---

### Task 7: Snapshot and LiveSource carry summaries

**Files:**
- Modify: `src/cardak/core/snapshot.py`, `src/cardak/core/source.py`, `src/cardak/cli.py` (`_deck` passes `key_file=anthropic_key_path()`)
- Test: `tests/test_snapshot.py`, `tests/test_source.py`

**Interfaces:**
- Consumes: Task 2 store methods, `month_start`, `key_warning`, `SummaryInfo` (Task 3).
- Produces:
  - `AgentView.summary: str | None = None` — set only when `status == "waiting"` and the session id is in `summaries`
  - `build_snapshot(..., summaries: Mapping[str, str] | None = None)` — session_id → text
  - `DeckSnapshot.summary_capped: bool = False`
  - `AgentStore` protocol gains `summaries_for` and `spend_since`
  - `LiveSource(..., key_file: Path | None = None)`; `LiveSource.summary_info(state: SessionState) -> SummaryInfo | None` — `None` when summaries are disabled or `key_file` is `None`; else the summary text for that state's current wait (or `None`) with this month's spend and the limit
  - Each poll: `summaries_for` over live sessions whose status is `waiting` with their `status_since`; `summary_capped = enabled and spend_since(month_start(now)) >= limit`; `key_warning(key_file)` appended to the warnings when not `None`. A `StoreError` here sets the existing store warning and yields no summaries.

- [ ] **Step 1: Write the failing tests**

```python
def test_waiting_agent_gets_its_summary() -> None:   # test_snapshot.py
    snap = build(sessions=[waiting_state("s")], summaries={"s": "Did x; needs y"})
    assert row_for(snap).agent.summary == "Did x; needs y"

def test_working_agent_gets_no_summary() -> None:
    snap = build(sessions=[working_state("s")], summaries={"s": "stale"})
    assert row_for(snap).agent.summary is None

def test_source_reads_summaries_for_current_waits(...) -> None:   # test_source.py, real Store in tmp_path
    # waiting session status_since=10.0; summaries rows for (s, 10.0) and an older (s, 5.0)
    # → poll().groups[0] (waiting group) row agent.summary == text of the 10.0 row
def test_source_flags_the_cap(...) -> None:      # spend 5.0 this month, limit 5 → summary_capped True; 4.99 → False
def test_source_warns_about_an_open_key(...) -> None:   # key chmod 0o644 → exact key-warning copy in config_warnings
def test_summary_info(...) -> None:   # SummaryInfo("Did x; needs y", 0.42, 5.0); disabled config → None
```

- [ ] **Step 2: Run** `uv run pytest tests/test_snapshot.py tests/test_source.py -q` — Expected: FAIL.

- [ ] **Step 3: Implement** as in Interfaces. Fetch summaries and spend inside the same `_lock`-held `_sessions` path; the clock is `self._clock`.

- [ ] **Step 4: Run** `uv run pytest -q` — Expected: PASS.

- [ ] **Step 5: Commit** `feat(core): the deck snapshot carries waiting summaries (#30)`

---

### Task 8: The deck shows tag + summary

**Files:**
- Modify: `src/cardak/ui/widgets.py`, `src/cardak/ui/detail.py`, `src/cardak/ui/app.py`
- Test: `tests/test_widgets.py`, `tests/test_detail.py`, `tests/test_app.py`

**Interfaces:**
- Consumes: `AgentView.summary`, `DeckSnapshot.summary_capped`, `LiveSource.summary_info` (Task 7), `TAGS` (Task 3).
- Produces:
  - `widgets._reason(agent)`: with a summary, `"<tag>  <summary>"`; without, unchanged. `waiting_label` renders the tag `bold WAITING` and the summary plain; `reason_label` (`↳`) uses the same text. Both keep the `MAX_REASON` cut.
  - `detail_text(state, project, now, info: SummaryInfo | None = None)`: under NEEDS, when `info` and `info.text`: `  Summary: <text>` before the raw reason; at the end, when `info`: `\nSummaries this month: $<spend:.2f> of $<limit:.2f>`.
  - `DeckSource` protocol (ui/app.py) gains `summary_info(state) -> SummaryInfo | None`; `action_detail` passes it to `DetailScreen(state, project, info)`.
  - `DeckApp`: the first snapshot with `summary_capped` triggers one `notify(cap notice, severity="warning")` per run.

- [ ] **Step 1: Write the failing tests**

```python
def test_waiting_label_with_summary() -> None:
    text = waiting_label(row(kind="permission", reason="Bash: cargo test", summary="Fixed it; wants tests"), now)
    assert "approve  Fixed it; wants tests" in text.plain and "cargo test" not in text.plain

def test_waiting_label_without_summary_is_unchanged() -> None:
    assert "Approve a tool: Bash: cargo test" in waiting_label(row(kind="permission",
                                                   reason="Bash: cargo test"), now).plain

@pytest.mark.parametrize("kind, tag", [("permission", "approve"), ("question", "decide"), ("finished", "review")])
def test_reason_line_uses_the_tag(kind: str, tag: str) -> None:
    assert reason_label(row(kind=kind, summary="S")).plain.strip() == f"↳ {tag}  S"

def test_detail_shows_summary_and_spend() -> None:
    plain = detail_text(waiting_state(), "api-gw", now, SummaryInfo("Did x; needs y", 0.42, 5.0)).plain
    assert "Summary: Did x; needs y" in plain and "Summaries this month: $0.42 of $5.00" in plain

def test_detail_without_info_is_unchanged() -> None: ...   # no "Summar" in plain

async def test_cap_notice_shows_once() -> None:   # test_app.py, StubSource returning summary_capped=True twice
    # app.notify called once with the exact cap-notice copy for limit 5.0
```

- [ ] **Step 2: Run** `uv run pytest tests/test_widgets.py tests/test_detail.py tests/test_app.py -q` — Expected: FAIL.

- [ ] **Step 3: Implement** as in Interfaces. Stub sources in existing UI tests gain a `summary_info` returning `None`.

- [ ] **Step 4: Run** `uv run pytest -q` — Expected: PASS.

- [ ] **Step 5: Commit** `feat(ui): waiting lines show a tag and a summary (#30)`

---

### Task 9: Docs, architecture check, live check

**Files:**
- Modify: `README.md`, `AGENTS.md`, `CHANGELOG.md`

- [ ] **Step 1: Docs.**
  - README "Summaries" section: what it shows; create `~/.config/cardak/anthropic_key` and `chmod 600` it; what is sent to Anthropic (the spec's list, in plain words); `[summary] monthly_limit_usd` (default 5, 0 off); the cost estimate.
  - AGENTS.md: layering line `cli → {ui, wezterm, claude, llm} → core → collectors` and "`llm/` imports nothing from cardak"; files table rows for `~/.config/cardak/anthropic_key` (read by `cardak summarize`, never written) and the `summaries` table in `cardak.db` (written by `cardak summarize`); in the hook-budget paragraph, one line that `cardak summarize` runs detached and is not budgeted, and the guard now also excludes `anthropic`/`cardak.llm`/`core.summary(ize)`.
  - CHANGELOG under `## Unreleased` / `### Added`: one line for the deck user, ending `(#30)`.

- [ ] **Step 2: Full gate.** `uv run ruff check && uv run ruff format --check && uv run pyright && uv run pytest --cov --cov-branch --cov-fail-under=80 -q` — Expected: all clean, coverage ≥ 80 %.

- [ ] **Step 3: Architecture check.** enola `generate_snapshot` then `diff_snapshot` against the baseline pinned on `9a93dee`. Expected: new module `llm/` with no edge into `core`/`ui`; no new cycle; no layer crossed. Fix any finding before going on; report accepted hotspots by absolute size.

- [ ] **Step 4: Live check** (with the user's real key, in an isolated scratch setup per AGENTS.md: scratch `XDG_STATE_HOME`, passed explicitly with `env …`). In a scratch Claude session with `cardak hooks install --settings <scratch>`, produce one wait of each kind; for each: within a few seconds a `summaries` row exists (`sqlite3 <scratch>/cardak/cardak.db 'select * from summaries'`), the deck line shows tag + summary, `i` shows summary, raw reason and spend. Record the three real summaries and their costs for the PR description. If the API rejects a parameter, fix it in `llm/anthropic.py`, add a test, note it in the PR.

- [ ] **Step 5: Commit** `docs: waiting summaries (#30)`
