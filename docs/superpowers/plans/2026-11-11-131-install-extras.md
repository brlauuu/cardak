# #131 plan: install in layers

Spec: `docs/superpowers/specs/2026-11-11-131-install-extras-design.md`. Each task starts with a failing test.

1. **`shared/extras.py`**: `EXTRAS`, `INSTALL`, `missing(extra)` (cached `find_spec`) and `needs(extra, what)`.
   - Tests in `tests/test_shared_extras.py`.
   - A `blocked_extra` fixture in `tests/conftest.py` that patches the cache.
2. **Background**:
   - `backgrounds.images` and `ensure_art` return `[needs(...)]` and make nothing when the extra is missing.
   - `Tools.unavailable()`.
   - The picker takes `backgrounds_off: str | None`, and `ui/app.py` passes it.
   - Test that `themes apply` prints the warning.
3. **Anthropic**:
   - `config.summary_ready` is false for anthropic without the extra, and the hook test covers it.
   - `LiveSource._warnings_now` names the hint once.
   - The Summaries screen's anthropic row and detail show the hint, and `t` says it.
   - `cli._anthropic` gets a clear error if it's reached anyway: `cardak summarize` logs it.
4. **Packaging and CI**:
   - `pyproject.toml` optional dependencies and `uv lock`.
   - `test_packaging` checks the extras and the base.
   - `importorskip` in the tests that import Pillow, pyfiglet, resvg or anthropic.
   - A `base` job in `test.yml`; `test` syncs `--all-extras`.
   - Run the suite in a scratch venv without extras.
5. **Docs**: README install, `site/index.html`, AGENTS.md (Stack, rule, dev sync), CHANGELOG.
6. **Gate**: the gate, enola, the hook timing with `find_spec`, and a live install check of `uv tool install` into a scratch tool dir, base and `[all]`.
