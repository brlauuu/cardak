# Summary Providers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let waiting summaries come from one of five providers (anthropic, ollama, fireworks, openai, codex). Several can be set up and exactly one is in use, and a deck screen on `s` sets them up, tests them and switches between them.

**Architecture:** `core/config` parses `[summary]` into `SummarySettings` with the provider in use and one `ProviderSettings` per table. An error there marks only summaries as broken. `llm/` gains three providers (ollama, openai-compatible, codex) behind the existing `Summarizer` protocol; `cli.py` builds the right one from the settings and hands a factory to `cardak summarize` and to the deck. Readiness (the hook's and the deck's gate) and cost move from "anthropic_key exists / Haiku price" to the provider in use.

**Tech Stack:** Python 3.13, Textual, tomlkit, urllib (no new dependency), pytest.

**Spec:** `docs/superpowers/specs/2026-10-30-summary-providers-design.md`

## Global Constraints

- Provider names, in this order everywhere: `anthropic`, `ollama`, `fireworks`, `openai`, `codex`.
- No `provider` key means `"anthropic"`; existing configs behave exactly as before.
- A `[summary]` error fails only summaries: `load` still returns the projects, notify, resources and web settings.
- Defaults: ollama `url = "http://127.0.0.1:11434"`; fireworks URL fixed at `https://api.fireworks.ai/inference/v1`; anthropic model `summary.MODEL`.
- Key files: `~/.config/cardak/<name>_key` for anthropic, fireworks and openai. The openai key is optional. Keys are written atomically with mode 0600 and read with `summary.read_key`, never from environment variables.
- Time limits: 20 s for anthropic, fireworks and openai; 60 s for ollama and codex.
- Codex runs `codex exec --ephemeral --sandbox read-only --skip-git-repo-check --ignore-rules -o <file> [-m model] -` in an empty temp dir, with the prompt on stdin.
- Pricing: anthropic from `core/prices` (lookup in the PriceBook table). Fireworks from the `fireworks_ai/<model>` entries. Openai only when the URL host is `api.openai.com`. Everything else costs $0.
- The hook path imports nothing new (`tests/test_cli.py` guards); readiness is config + `stat` only.
- Layering: `llm/` imports nothing from cardak; `ui/` never imports `cardak.llm` (the factory is injected from `cli.py`).
- Tests never reach the network or run the real `codex`; fixtures are recorded live and neutralised.

## Review Focus

1. A `provider` naming a provider with no table, or a table with a wrong type: `load` must not raise, the deck must show the message, and the hook must spawn nothing (Task 1 `test_bad_provider_keeps_projects`, Task 4 `test_hook_skips_broken_summary_config`).
2. Ollama is up but the model isn't pulled, or the server is down: the run records a failed row with a readable error, and the deck's test shows it (Task 2 `test_ollama_http_error_raises_readable`).
3. Codex is missing from PATH or exits non-zero with no output file: a readable error, never a hang past 60 s (Task 2 `test_codex_failure_is_readable`, `test_codex_timeout`).
4. Switching providers in the screen while the file has comments keeps them (Task 1 `test_set_provider_keeps_comments`).
5. Saving a key through the screen leaves a 0600 file even when one with 0644 existed (Task 1 `test_write_key_is_private`).

---

### Task 1: Summary config, provider tables, writers and key files

**Files:**
- Modify: `src/cardak/core/config.py` (`SummarySettings`, `_summary`, new writers, `provider_key_path`)
- Modify: `src/cardak/core/summary.py` (`write_key`)
- Test: `tests/test_config.py`, `tests/test_summary.py`

**Interfaces:**
- Produces:
  - `PROVIDERS: tuple[str, ...] = ("anthropic", "ollama", "fireworks", "openai", "codex")`, `KEYED = {"anthropic": True, "fireworks": True, "openai": False}` (value: key required), `OLLAMA_URL`, `FIREWORKS_URL` in `core/config.py`.
  - `@dataclass(frozen=True) ProviderSettings(url: str | None = None, model: str | None = None)`.
  - `SummarySettings(monthly_limit_usd=5.0, provider="anthropic", providers: Mapping[str, ProviderSettings] = {}, error: str | None = None)`. `enabled` stays `monthly_limit_usd > 0 and error is None`. Add `active -> ProviderSettings` (the in-use table, or the empty `ProviderSettings()` for anthropic with none).
  - `provider_key_path(name: str, env=None) -> Path`. `anthropic_key_path` stays and equals `provider_key_path("anthropic")`.
  - `summary_ready(summary: SummarySettings, env=None) -> bool`: enabled, and the key file exists when `KEYED.get(provider)` is True. Uses `stat`/`exists` only.
  - `set_summary_provider(path: Path, name: str) -> None`, `set_provider(path: Path, name: str, url: str | None, model: str | None) -> None` (writes or replaces `[summary.<name>]`; None leaves that key out), `remove_provider(path: Path, name: str) -> None` (raises `ConfigError` for the provider in use). All go through tomlkit like `set_web_enabled`. `set_summary_provider` refuses a name that isn't set up (`ConfigError`); anthropic counts as set up when its key file exists (spec); `remove_provider("anthropic")` removes only its table.
  - `summary.write_key(path: Path, key: str) -> None`: atomic, mode 0600, `key.strip()` plus a newline.

- [ ] **Step 1: Write the failing tests** in `tests/test_config.py`:

```python
def test_summary_defaults_to_anthropic(tmp_path):
    cfg = load(_write(tmp_path, "[summary]\nmonthly_limit_usd = 3\n"))
    assert cfg.summary.provider == "anthropic" and cfg.summary.error is None

def test_summary_provider_tables(tmp_path):
    cfg = load(_write(tmp_path, '[summary]\nprovider = "ollama"\n[summary.ollama]\nmodel = "m"\n'))
    assert cfg.summary.active == ProviderSettings(url="http://127.0.0.1:11434", model="m")

@pytest.mark.parametrize("text", [
    '[summary]\nprovider = ["ollama", "codex"]\n',
    '[summary]\nprovider = "gpt"\n',
    '[summary]\nprovider = "fireworks"\n',                          # no table
    '[summary]\nprovider = "openai"\n[summary.openai]\nmodel = "m"\n',  # no url
    '[summary.ollama]\nmodel = 3\n',
])
def test_bad_provider_keeps_projects(tmp_path, text):
    cfg = load(_write(tmp_path, '[[project]]\nname = "webshop"\npath = "/w"\n' + text))
    assert cfg.projects[0].name == "webshop"
    assert cfg.summary.error and not cfg.summary.enabled

def test_set_provider_keeps_comments(tmp_path):
    path = _write(tmp_path, "# mine\n[summary] # cap\nmonthly_limit_usd = 2\n")
    set_provider(path, "ollama", None, "gemma4:e4b")
    set_summary_provider(path, "ollama")
    assert "# mine" in path.read_text() and "# cap" in path.read_text()
    assert load(path).summary.provider == "ollama"

def test_set_summary_provider_refuses_unset(tmp_path): ...   # ConfigError, file unchanged
def test_remove_provider_refuses_in_use(tmp_path): ...       # ConfigError
def test_summary_ready(tmp_path): ...  # anthropic without key → False; with key → True;
                                       # ollama with table → True; fireworks without key → False;
                                       # openai without key → True; monthly_limit 0 → False
```

and in `tests/test_summary.py`:

```python
def test_write_key_is_private(tmp_path):
    path = tmp_path / "fireworks_key"
    path.write_text("old"); path.chmod(0o644)
    write_key(path, "  fw-123 \n")
    assert path.read_text() == "fw-123\n" and path.stat().st_mode & 0o777 == 0o600
    assert read_key(path) == "fw-123"
```

- [ ] **Step 2: Run them.** `uv run pytest tests/test_config.py tests/test_summary.py -q`. Expected: FAIL (ImportError on `ProviderSettings`).
- [ ] **Step 3: Implement.** `_summary` catches its own problems and returns `SummarySettings(..., error=message)` instead of raising. Only a non-table `[summary]` keeps raising, as today (it isn't a provider problem). Unknown keys inside a provider table are ignored.
- [ ] **Step 4: Run them.** Expected: PASS. Then run the whole suite: `uv run pytest -q`.
- [ ] **Step 5: Commit** `feat(core): summary provider settings and writers (#47)`.

### Task 2: Providers in `llm/` with recorded fixtures

**Files:**
- Create: `src/cardak/llm/http.py`, `src/cardak/llm/ollama.py`, `src/cardak/llm/openai.py`, `src/cardak/llm/codex.py`
- Create: `tests/fixtures/llm/ollama_chat.json`, `ollama_tags.json`, `openai_chat.json`, `codex_exec.jsonl`
- Test: `tests/test_llm_providers.py`

**Interfaces:**
- Produces (each summarizer has `.summarize(system, user) -> Reply`, using `llm.anthropic.Reply`, and normalises its finish reason to `"end_turn"` when the model stopped on its own, so `summary.validate` works unchanged):
  - `llm/http.py`: `post_json(url: str, body: dict, headers: dict[str, str], timeout: float) -> dict` and `get_json(url, timeout) -> dict`. On an HTTP error it raises `ProviderError(f"HTTP {code}: {body excerpt}")`; on a connection error, `ProviderError("can't reach <url>: <reason>")`.
  - `OllamaSummarizer(url: str, model: str, post=post_json)`, `TIMEOUT = 60.0`, `list_models(url: str, get=get_json) -> list[str]`.
  - `OpenAISummarizer(url: str, key: str | None, model: str, post=post_json)`, `TIMEOUT = 20.0`.
  - `CodexSummarizer(model: str | None, run=subprocess.run)`, `TIMEOUT = 60.0`, `codex_version(run=subprocess.run) -> str | None` (None when not found).
  - `ProviderError(Exception)` in `llm/http.py`; codex raises it too.
- [ ] **Step 1: Record fixtures** against the local Ollama (`/api/chat`, `/api/tags`, and `/v1/chat/completions` as the OpenAI-compatible shape) and one `codex exec --json` run on a neutral prompt in a scratch dir. Replace anything personal (paths, ids, model lists beyond two neutral names) before saving.
- [ ] **Step 2: Write the failing tests:**

```python
def test_ollama_parses_reply():          # fake post returns the fixture; Reply text, tokens, "end_turn"
def test_ollama_sends_system_and_model(): # captured body: model, stream False, messages[0].role == "system"
def test_ollama_http_error_raises_readable():  # ProviderError message contains "HTTP 404"
def test_list_models():                   # names from ollama_tags.json
def test_openai_parses_reply_and_sends_bearer():  # Authorization only when key given
def test_openai_length_finish_is_not_end_turn():  # finish_reason "length" → stop_reason "length"
def test_codex_builds_command(tmp_path):  # fake run sees the flags in Global Constraints, prompt on stdin
def test_codex_reads_last_message():      # fake run writes the -o file; tokens from the jsonl usage event
def test_codex_failure_is_readable():     # non-zero exit, no file → ProviderError with stderr excerpt
def test_codex_timeout():                 # run raises TimeoutExpired → ProviderError("codex timed out after 60 s")
def test_codex_version_missing():         # FileNotFoundError → None
def test_llm_imports_nothing_from_cardak():  # extend the existing layering test if there is one
```

- [ ] **Step 3: Run them.** `uv run pytest tests/test_llm_providers.py -q`. Expected: FAIL (ImportError).
- [ ] **Step 4: Implement.** `urllib.request` with JSON. Ollama: `{"model", "messages", "stream": false, "options": {"num_predict": 256}}`; the reason comes from `done_reason == "stop"`. Codex input: the system prompt, a blank line, then the user text.
- [ ] **Step 5: Run them.** Expected: PASS. Then `uv run pytest -q`.
- [ ] **Step 6: Commit** `feat(llm): ollama, openai-compatible and codex summarizers (#47, #48, #49)`.

### Task 3: Prices for every provider

**Files:**
- Modify: `src/cardak/collectors/prices.py` (`select`)
- Modify: `src/cardak/core/summary.py` (replace `PRICES`/`cost`)
- Test: `tests/test_prices_collector.py` (or wherever `select` is tested), `tests/test_summary.py`

**Interfaces:**
- Produces:
  - `select` keeps today's Anthropic entries, plus `fireworks_ai/…` keys with `litellm_provider == "fireworks_ai"` and slash-free keys with `litellm_provider == "openai"`.
  - `summary.cost(provider: str, settings: ProviderSettings, model: str, input_tokens: int, output_tokens: int, table: Mapping[str, ModelPrice]) -> float`.
  - `summary.cost_note(provider: str, settings: ProviderSettings, table) -> str`, one of: `"local, $0"` (ollama), `"your Codex plan, $0"` (codex), `"custom server: no price, the monthly cap doesn't cover it"` (openai with another host), `"no price known: the monthly cap doesn't cover it"`, or `"$<in>/$<out> per M tokens"`.
- [ ] **Step 1: Failing tests:** `select` on a small JSON with one entry of each kind plus a `bedrock/…` one, which is dropped. `cost("anthropic", …, "claude-sonnet-5-5", 1_000_000, 0, BUILTIN) == 2.0`. Fireworks with `accounts/fireworks/models/x` priced from `fireworks_ai/accounts/fireworks/models/x`. Openai at `http://127.0.0.1:8080/v1` costs 0. `api.openai.com` is priced. An unknown model costs 0. `cost_note` gets one case each.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.** Use `core.prices.lookup` and `ModelPrice.base` input/output rates (no cache figures for summaries). Anthropic falls back to `MODEL`'s price when the reply's model is unknown, as today's comment explains. `spend.py` still finds Claude models: the new entries have other names.
- [ ] **Step 4: Run them.** Expected: PASS. Then `uv run pytest -q`.
- [ ] **Step 5: Commit** `feat(core): price summaries per provider (#48)`.

### Task 4: The run, the hook gate and the deck gate

**Files:**
- Modify: `src/cardak/core/summarize.py`, `src/cardak/core/hook.py`, `src/cardak/core/source.py`, `src/cardak/core/daemon.py`, `src/cardak/cli.py` (`_summarize`, `_deck`, a `_make_summarizer`)
- Test: `tests/test_summarize.py`, `tests/test_hook.py`, `tests/test_source.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: Task 1 (`SummarySettings.active`, `summary_ready`, `provider_key_path`, `KEYED`), Task 2 classes, Task 3 `cost`.
- Produces:
  - `MakeSummarizer = Callable[[str, ProviderSettings, str | None], Summarizer]` (name, settings, key or None) in `core/summarize.py`; `run(..., keys_dir: Path, prices: Mapping[str, ModelPrice], make_summarizer)` replaces `key_file`.
  - `cli._make_summarizer(name, settings, key) -> Summarizer` (imports `cardak.llm.*` lazily).
  - Hook: `_ping` spawns only when `summary_ready(config.summary, env)`; its `key_file` parameter becomes `env`-based. On `ConfigError` the hook keeps falling back to `Config()`, but `_summary` no longer raises for provider problems, so the bad-provider case is covered by `summary.error`.
  - `LiveSource(key_file=…)` becomes `LiveSource(summaries_env: Mapping[str, str] | None = None)` (None: no summaries in this deck). `_summary_store` uses `summary_ready`. `key_warning` checks the in-use provider's key file (the message names that file). `summary.error` shows as a warning line: `f"waiting summaries off: {error}"`.
- [ ] **Step 1: Failing tests:**
  - `test_run_uses_active_provider`: ollama config; the fake factory records `("ollama", settings, None)`; the stored row has the reply's model and cost 0.
  - `test_run_reads_provider_key`: fireworks with `fireworks_key` → the factory gets the key. A missing key records the error `"fireworks_key is missing or empty"`.
  - `test_run_openai_without_key`: the key is None and the run goes ahead.
  - `test_run_records_provider_error`: the factory raises `ProviderError("can't reach …")` → a failed row with that text.
  - `test_hook_skips_broken_summary_config`: `provider = "gpt"` with an anthropic key present → nothing spawned.
  - `test_hook_spawns_for_ollama_without_key`.
  - `test_source_warns_on_summary_error`: the warning line is present and there are no summaries.
  - `test_source_key_warning_names_active_key`.
  - `test_summarize_command_builds_ollama` in `test_cli.py`: `_make_summarizer("ollama", ProviderSettings(url, "m"), None)` is an `OllamaSummarizer`.
  - The existing hook import guards still pass.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.** `cardak summarize` builds `PriceBook(cache_dir()/CACHE_NAME).current()` for prices, reading the cache only with no download (check that `current` never fetches).
- [ ] **Step 4: Run them.** Expected: PASS. Then run `uv run pytest -q`, then `python -X importtime -c "import cardak.cli" 2>&1 | grep -E "llm|urllib"`. Expected: nothing.
- [ ] **Step 5: Commit** `feat: summaries from the provider in use (#47, #48, #49)`.

### Task 5: The Summaries screen (`s`)

**Files:**
- Create: `src/cardak/ui/summaries.py` (`SummariesScreen`)
- Modify: `src/cardak/ui/app.py` (binding `Binding("s", "summaries", "Summaries")`, the `providers` argument), `src/cardak/core/source.py` (writer methods), `src/cardak/core/summary.py` (`SAMPLE_INPUT`), `src/cardak/cli.py` (passing `ProviderTools`)
- Test: `tests/test_summaries_screen.py`, `tests/test_source.py`

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces:
  - `summary.SAMPLE_INPUT: str`: a fixed `build_input` output for a made-up project (`webshop`), a permission wait on `Bash: rm -rf build/`, and neutral counts.
  - `@dataclass(frozen=True) ProviderTools` in `core/summarize.py`: `make: MakeSummarizer`, `list_models: Callable[[str], list[str]]`, `codex_version: Callable[[], str | None]`, `prices: Callable[[], Mapping[str, ModelPrice]]`.
  - `LiveSource.use_provider(name)`, `.save_provider(name, url, model, key: str | None)` (writes the key file only when `key` is non-empty), `.remove_provider(name)`. Each returns `str | None` (an error message), reloads the config and runs on the caller's thread like `set_theme`.
  - `DeckApp(..., providers: ProviderTools | None = None)`; `s` opens the screen only when it's given, else a notification "summaries aren't available in this deck".
  - `SummariesScreen(source, tools)`:
    - left list in `PROVIDERS` order with markers ● ○ · and "in use" / "set up" / "—";
    - right pane with the settings, `cost_note`, and the last test; the top line shows `this month $x of $y` (from `source.summary_info`-like spend; add `LiveSource.summary_spend() -> float | None`);
    - keys `enter` use, `t` test, `n`/`e` form, `delete` remove (through `ConfirmScreen`), `escape` close;
    - the form uses `Input`s (key `password=True`) and for ollama a `Select` filled from `list_models` in a worker, falling back to an `Input` when that fails; its buttons are Test / Save / Save and use;
    - a test runs `tools.make(...).summarize(SYSTEM_PROMPT, SAMPLE_INPUT)` in a thread worker and shows `✓ 12.4 s` plus the `validate`d line, or `✗` plus the error (on a paid provider the pane says a test costs a fraction of a cent and is not counted toward the cap);
    - when `config.summary.error` is set, it's shown on top.
- [ ] **Step 1: Failing tests** (Textual `run_test` with a fake source and fake tools):
  - `test_lists_providers_in_order_with_markers`;
  - `test_enter_switches_provider` (the source's `use_provider` is called, and the marker moves after the reload);
  - `test_enter_refuses_unset` (a notification; nothing called);
  - `test_test_runs_in_background_and_shows_line` (the fake make returns a canned Reply; the pane shows `✓` and the line);
  - `test_test_shows_error`;
  - `test_setup_ollama_lists_models` and `test_setup_ollama_without_server_falls_back_to_input`;
  - `test_save_and_use_writes_key_and_switches` (against a real `LiveSource` on tmp config: `fireworks_key` is 0600 and `provider == "fireworks"`);
  - `test_remove_asks_and_refuses_in_use`;
  - `test_shows_summary_error`;
  - `test_s_without_tools_notifies` in `test_app.py`.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement**, following `theme_picker.py`'s layout and CSS conventions.
- [ ] **Step 4: Run them.** Expected: PASS. Then `uv run pytest -q`, `uv run ruff check`, `uv run pyright`.
- [ ] **Step 5: Commit** `feat(ui): summaries screen to set up, test and switch providers (#47, #48, #49)`.

### Task 6: Measure, check live, document

**Files:**
- Modify: `README.md` (waiting summaries: providers, the `s` screen), `AGENTS.md` (layout lines for the new `llm/` modules and `ui/summaries.py`; files table rows for `fireworks_key` / `openai_key`; a "### Summary providers (#47–#49)" section with the measured latencies, the codex flags, the pricing rules and the readiness gate; Architecture checks), `CHANGELOG.md` (Unreleased → Added)
- [ ] **Step 1: Measure** the time per summary with `SAMPLE_INPUT` on the local Ollama (`gemma4:e4b`, 5 runs, median) and with codex (3 runs), and the deck's CPU while a local summary runs. Record the numbers without machine details.
- [ ] **Step 2: Live check** in scratch XDG dirs: the deck headless (or in a scratch WezTerm), `s`, set up ollama, test, save and use, then a `cardak summarize` against a scratch store row writes a summary from ollama. Never touch the real config or state.
- [ ] **Step 3: Run the checks.** `uv run ruff check && uv run ruff format --check && uv run pyright && uv run pytest --cov --cov-branch --cov-fail-under=80 -q`, then `enola check .`. Fix new findings or record the accepted ones in AGENTS.md.
- [ ] **Step 4: Commit** `docs: summary providers (#47, #48, #49)`.
