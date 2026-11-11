# #131: install in layers

## Decisions (brainstorm, 2026-11-11)

- Two extras plus `all`:
  - `background`: Pillow, pyfiglet and resvg-py, about 33 MB installed.
  - `anthropic`: the Anthropic SDK, about 37 MB with httpx and pydantic.
  - The web view stays in the base install: it is the standard library plus segno (0.5 MB).
- A plain install is the slim base. README and the landing page show `cardak[all]` as the main command and the base as an option. Python packaging has no supported way to make extras install by default.
- A missing extra greys out what depends on it and gives a hint naming the install command. Nothing fails on import.
- CI gets a second job without extras that runs the whole suite. Tests that need an extra skip themselves. The main job keeps every extra and the 80% coverage floor.

## Base install

textual, psutil, tomlkit, tomli-w, segno. These cover the deck, agent status, themes, resources and alerts, the daemon, the web view, the PM view, and the stdlib summary providers (ollama, fireworks, openai, codex).

## `cardak/shared/extras.py` (stdlib only)

```python
EXTRAS = {"background": ("PIL", "pyfiglet", "resvg_py"), "anthropic": ("anthropic",)}
INSTALL = "uv tool install --reinstall 'cardak[all] @ git+https://github.com/brlauuu/cardak'"

def missing(extra: str) -> bool        # any module's importlib.util.find_spec is None; cached
def needs(extra: str, what: str) -> str
    # f"{what} needs cardak's {extra} extra: {INSTALL}"
```

`find_spec` reads only the import system's metadata and imports nothing. The cached answer is per process: a `uv tool install` replaces the deck's environment anyway, and the deck then has to restart.

## Background (`core/backgrounds.py`, `ui/theme_picker.py`)

- **`images()` and `ensure_art()`:** when `missing("background")` and some project wants art, they return no images and one problem: `needs("background", "the project background")`. This already becomes a banner line in the deck and a `warning:` line from `cardak themes apply`. `[background] enabled = false`, or every project's background being off, keeps it silent.
- **`Tools.enabled()`:** stays `[background] enabled`. A new `Tools.unavailable() -> str | None` returns the hint.
- **The picker** takes `backgrounds_off: str | None`, the reason, in place of the `backgrounds_on` bool. When set, the checkbox is disabled and the art line shows the reason. Today's reason is "background off for every project: [background] enabled = false"; the missing-extra hint is new. Previews carry no art.
- **`cardak.lua`** is unchanged. A project without an image gets no layer.

## Anthropic (`core/config.summary_ready`, `ui/summaries.py`, `cli._make_summarizer`)

- **`summary_ready`:** for `provider == "anthropic"`, it also requires `not missing("anthropic")`. The hook therefore never spawns a `cardak summarize` that can only fail. This costs one cached `find_spec` on a ping. Measure it against the hook budget, and update `test_hook_imports_only_what_it_needs` if that test lists allowed modules.
- **Summaries screen:** the anthropic row says the hint in place of its status, and its test button is disabled. The other providers are unaffected.
- **Hint in the deck:** `LiveSource` already warns about a summary setup problem. When the anthropic extra is missing, that warning gives the hint, once.
- **`cardak summarize`:** if it is reached anyway (a config raced), it logs the hint to `hook.log` instead of a traceback.

## Packaging (`pyproject.toml`)

```toml
dependencies = ["psutil", "segno", "textual", "tomli-w", "tomlkit"]
[project.optional-dependencies]
background = ["pillow>=12", "pyfiglet>=1.0.4", "resvg-py>=0.5.0"]
anthropic = ["anthropic>=2.0.0"]
all = ["cardak[background,anthropic]"]
```

- The dev env syncs with `uv sync --all-extras`, and CI and AGENTS.md say so: uv has no setting that turns extras on by default.
- `tests/test_packaging.py`: the wheel's metadata has the three extras, and the base `Requires-Dist` has none of the extras' packages.

## Tests

- **Shared tests:** `tests/test_shared_extras.py` covers `missing` against a module that exists and one that doesn't, the cache, and `needs`' text.
- **A missing-extra fixture:** `blocked_extra(monkeypatch, name)` makes `missing(name)` true. With it:
  - `images` and `ensure_art` return the hint and make no files.
  - The picker greys the switch and shows the hint.
  - `summary_ready` is false for anthropic and true for ollama.
  - The Summaries screen's anthropic row shows the hint.
  - `themes apply` prints the warning.
- **Tests that import an extra:** `test_ascii_art`, the drawing tests in `test_backgrounds` and `test_llm_anthropic` start with `pytest.importorskip(...)`. Whole modules skip only where every test needs the library; elsewhere the skip is per test.
- **CI:**
  - A new `base` job runs `uv sync --locked`, which installs the dev group and no extras, then `uv run pytest -q`.
  - The `test` job switches to `uv sync --locked --all-extras` and is otherwise unchanged.
  - Branch protection requires only `test`. The PR asks whether to make `base` required too.

## Docs

- **README, install section:** `cardak[all]` first, then the base install and what each extra adds. Add the editable dev install, `uv tool install --editable '.[all]'`.
- **site/index.html:** the install line becomes `[all]`.
- **AGENTS.md:** Stack, the extras rule ("an extra's library is imported only inside the code that needs it, behind `shared.extras.missing`"), and the dev sync command.
- **CHANGELOG, Changed:** a plain install is now the slim base, and to keep everything, install `cardak[all]`.

## Versioning

This changes what a plain `uv tool install …` gives an existing user: they lose the art and Anthropic summaries until they reinstall with `[all]`. That is something a user must act on, so it is a **minor** bump on 0.x (0.18.0) with a CHANGELOG line saying exactly what to do. The release is not part of this PR.
