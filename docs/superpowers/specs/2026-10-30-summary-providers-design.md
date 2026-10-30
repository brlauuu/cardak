# Summary providers (#47, #48, #49)

## Intent

#30 summarizes each waiting Claude session on its "Waiting on you" line through Anthropic's API.
The user wants **options to choose from** for who writes those lines, to save cost or to use
another agent they already pay for: a local open-weight model (#47), Fireworks (#48), and Codex
(#49) or an OpenAI-compatible key. Several providers can be set up at once; **exactly one is in
use**, and it must be obvious which. Setting one up, testing it and switching between them
happens **in the deck**.

## Goals

- Four more providers behind the existing `Summarizer` protocol (`core/summary.py`):
  `ollama`, `openai` (any OpenAI-compatible server), `fireworks` (the same kind, with Fireworks'
  URL filled in) and `codex` (`codex exec`). `anthropic` stays and stays the default.
- Same job as #30: same input (`summary.build_input`), same system prompt, same one line out,
  checked by `summary.validate`. Failures fall back to the raw reason, as today.
- A **Summaries screen** in the deck, opened with `s`, to set up, edit, test, remove and switch
  providers.
- The monthly cap keeps meaning money: paid APIs are priced; local and plan-based providers
  cost $0.

## Non-goals

- No fallback chain: when the provider in use fails, the line shows the raw reason. Nothing
  tries the next provider.
- No switching from the phone page; the web view stays read-only.
- No tracking of Codex *sessions* in the deck (status, waits, jumping). That's a separate,
  later issue.
- cardak doesn't run or manage a local model server; it talks to one that's already running.

## Anti-goals

- Two providers in use at once, or any doubt about which one is.
- A summary or a test that blocks the deck or the hook. The hook's 150 ms budget and import
  guards (`tests/test_cli.py`) still hold: no provider code, no HTTP client, nothing new
  imported on the hook's path.
- API keys in `projects.toml`, or a key file other users can read.
- Codex running tools, touching files, or leaving a session behind for a summary.

## Configuration

```toml
[summary]
provider = "ollama"          # the one in use; absent means "anthropic"
monthly_limit_usd = 5.0      # unchanged; 0 still turns summaries off

[summary.ollama]
url = "http://127.0.0.1:11434"
model = "gemma4:e4b"

[summary.fireworks]
model = "accounts/fireworks/models/…"

[summary.openai]
url = "https://api.openai.com/v1"   # or a llama.cpp / vLLM server
model = "…"

[summary.codex]
model = "…"                  # optional; codex's own default when absent
```

- **One key decides.** `provider` is a single string naming one of `anthropic`, `ollama`,
  `openai`, `fireworks`, `codex`. A list, another type, an unknown name, or a name with no
  `[summary.<name>]` table (except `anthropic`, which needs none) is an error with a message
  saying which. This is the "error when several are active" case: TOML can't hold two values
  in one key, so there's no other way to write it.
- **A `[summary]` error fails only summaries** (my call). Today a `ConfigError` drops the whole
  file, and the hook and `cardak summarize` then fall back to `Config()`, which would mean
  Anthropic summaries keep running. Instead the config loads with the summary settings marked
  broken (the message kept). No summary is spawned or written. The deck shows the message as a
  warning line, the way it shows "waiting summaries failing", and keeps the projects, sounds and
  everything else. The same applies to a bad table under `[summary.<name>]`.
- Each table's required keys: `ollama`: `model` (`url` defaults to
  `http://127.0.0.1:11434`); `fireworks`: `model` (URL fixed,
  `https://api.fireworks.ai/inference/v1`); `openai`: `url`, `model`; `codex`: nothing.
  `anthropic` may have a table with `model`, else `summary.MODEL`.
- Keys: `~/.config/cardak/<provider>_key` (`fireworks_key`, `openai_key`; `anthropic_key` as
  today), read with `summary.read_key` (refused with group/other bits). `openai` with a local
  URL may have no key file; then no `Authorization` header is sent. Never read from environment
  variables (as #30 avoids `ANTHROPIC_API_KEY`).
- Existing configs with no `provider` keep working unchanged.

## Providers (in `llm/`, which imports nothing from cardak)

All return a `Reply` (text, model, stop reason, input and output tokens); a missing token count
is 0.

- **ollama**: `POST <url>/api/chat` with `stream: false`, the system and user messages, and a
  short output limit; tokens from `prompt_eval_count` / `eval_count`. Model list for the setup
  screen from `GET <url>/api/tags`.
- **openai / fireworks**: `POST <url>/chat/completions`, Bearer key when there is one;
  tokens from `usage`. Plain HTTP with the standard library (no new SDK dependency); my call,
  since one endpoint is all it uses.
- **codex**: `codex exec` with the prompt on stdin, run in an empty temporary directory,
  `--ephemeral --sandbox read-only --skip-git-repo-check --ignore-rules`, `-m` when a model is
  set, and `-o <file>` for the last message. The system prompt goes at the top of the input and
  tells it to answer only, without tools. Tokens from `--json` events when they're there, else
  0. Found on `PATH` (the deck's and the spawned job's).
- **Time limits**: 20 s for the hosted APIs (as today), 60 s for `ollama` and `codex`. A local
  model on CPU was expected at 10–20 s; measure on a CPU-only dev machine (a 4B-class model, no
  GPU) before shipping and record it in AGENTS.md.

## Cost and the cap

- **anthropic:** priced from the same price table the monthly spend uses (`core/prices`, with
  `BUILTIN` filling in), so a model other than Haiku is charged at its own rate. Today
  `summary.cost` charges every model at Haiku's (my call).
- **fireworks:** priced from LiteLLM's list. Its entries are keyed `fireworks_ai/<model id>`
  while replies carry `accounts/fireworks/models/…`, so the lookup adds the prefix. The cached
  list keeps only Anthropic entries today (`collectors/prices.py`); it keeps Fireworks' and
  OpenAI's entries now.
- **openai:** priced from LiteLLM's OpenAI entries **only when the URL's host is
  `api.openai.com`** (my call). Any other URL (llama.cpp, vLLM, a gateway) is stored at $0 and
  labelled "custom server: no price, the monthly cap doesn't cover it". That way a local model
  named `gpt-4o` isn't charged phantom spend.
- A fireworks or OpenAI model with no listed price is stored at $0, labelled "no price known:
  the monthly cap doesn't cover it".
- `ollama`, `codex`: $0, always. The screen labels them "local, $0" and "your Codex plan, $0".
- The cap is checked as today, against `summaries.cost_usd` this month, whichever provider
  wrote it. The `summaries.model` column already records which model wrote each line;
  whether to add a provider column is the builder's call (any change to an existing table must
  raise `MIN_READER`).

## Hook and deck gates

Today the hook spawns `cardak summarize` only when `anthropic_key` exists, and the deck shows
no summaries and no spend line without it (#52). Both become "**the provider in use is
ready**": its table is valid and, for keyed providers, its key file exists. For `ollama`,
`codex` and a keyless `openai` that's just the config. The check stays as cheap as today's
`exists()`: config and `stat` only, no network and no `PATH` search. The deck's existing
"key readable by others" warning applies to whichever key file is in use.

## The Summaries screen (`s`)

The layout is the user's choice, option A of the mockups:

```
 Summaries                                    this month $1.84 of $5.00
──────────────────────────┬──────────────────────────────────────────────
  ● anthropic   in use    │ ollama  (local, $0)
  ○ ollama      set up    │   url    http://127.0.0.1:11434
  ○ fireworks   set up    │   model  gemma4:e4b        (8 on server)
  · openai      —         │
  · codex       —         │   last test  ✓ 12.4 s
                          │   "Asks to approve: rm -rf build/"
──────────────────────────┴──────────────────────────────────────────────
 enter use  t test  n set up  e edit  del remove  esc close
```

- **Left:** the five providers, always in this order. ● is the one in use, ○ set up, · not
  set up.
- **Right:** the highlighted provider: its settings, what it costs ("local, $0", "your Codex
  plan, $0", "no price known…"), and its last test in this deck run (✓/✗, seconds, the line
  it wrote or the error).
- **`enter` use:** writes `provider = "<name>"` into `projects.toml` with `tomlkit`, keeping
  comments and layout. Refused, with a message, for a provider that isn't set up.
- **`n` set up / `e` edit:** a form in the right pane with that provider's fields:
  - ollama: URL (prefilled), model picked from the server's `/api/tags` list, or typed when
    the server doesn't answer;
  - fireworks: model, key;
  - openai: URL, model, key (optional);
  - codex: shows whether `codex` is found and its version; model optional;
  - anthropic: model (prefilled) and key.

  A **key** is typed or pasted into a masked field and written to its key file atomically with
  mode 0600. Left empty on edit, the existing key file is kept. Buttons: **Test**, **Save**,
  **Save and use**. Saving doesn't need a passing test.
- **`t` test:** runs one built-in **sample wait** (a fixed `build_input` from a made-up project
  and tool, kept in the code) through the provider, in a worker thread, with the provider's time
  limit. The deck stays usable meanwhile. It shows the time and the line `validate` returns, or
  the error. No real session text is ever sent by a test. A test is not stored in
  `summaries` and doesn't count toward the cap. It does cost a fraction of a cent on a paid
  API, and the screen says so.
- **`del` remove:** after a confirm dialog, removes the `[summary.<name>]` table. The key file
  is left in place; my call, since cardak never deletes a file the user may have made. Removing
  the provider in use is refused; switch first.
- **anthropic's states** (my call): it's set up when `anthropic_key` exists, with or without a
  table. With no `provider` key and no key file, it shows "● in use · no key" and summaries
  are off, as today. `del` on anthropic removes only its `[summary.anthropic]` table (the
  model override) and is refused while it is in use, like any other.
- `enter` is refused for a provider that isn't set up, the same rule for all five.
- An invalid `[summary]` (a bad `provider`) still opens the screen, with the error shown on top
  and `enter` usable to fix it by picking a provider.
- The footer shows `s Summaries`.

## Constraints

- Layering as AGENTS.md: providers in `llm/` (import nothing from cardak), the protocol and
  the run in `core`, the screen in `ui/`, wiring in `cli.py`. `cli._summarize` picks the
  provider from the config.
- Tests use recorded responses: an Ollama `/api/chat` and `/api/tags` reply, an OpenAI-style
  `/chat/completions` reply, and a `codex exec --json` run, recorded live and stripped of
  anything personal into `tests/fixtures/`. No test reaches the network or runs the real
  `codex`.
- Config writes go through `tomlkit`; key files are written atomically with mode 0600.
- README: a "Waiting summaries" update on providers and the `s` screen. AGENTS.md: files
  table rows for the new key files, the measured latencies, and the `codex exec` flags.
  CHANGELOG under Unreleased → Added.

## Left to the builder

- Widget choices for the form (Textual `Input` with `password=True` for keys, `Select` for the
  Ollama model list).
- Exact fields kept for "last test" while the deck runs.
- Pulling shared HTTP code for the two HTTP providers into one place in `llm/`.
