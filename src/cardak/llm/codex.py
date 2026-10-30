"""Waiting summaries from the Codex CLI, on the user's Codex plan (#49).

`codex exec` runs a whole agent; it gets an empty temporary directory, a read-only sandbox, no
saved session and no execpolicy rules, and is told to answer without tools.
"""

import json
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .anthropic import Reply
from .http import ProviderError

TIMEOUT = 60.0
MAX_ERROR = 300
ANSWER_ONLY = "Answer with the line only. Do not run commands or use tools."


def _usage(stdout: str) -> tuple[int, int]:
    """Tokens from the last `turn.completed` event of `--json` output; 0 when there is none."""
    found = (0, 0)
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        usage = event.get("usage") if isinstance(event, dict) else None
        if event.get("type") == "turn.completed" and isinstance(usage, dict):
            found = (int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0))
    return found


class CodexSummarizer:
    def __init__(self, model: str | None, run: Callable[..., Any] = subprocess.run) -> None:
        self.model = model
        self.run = run

    def summarize(self, system: str, user: str) -> Reply:
        with tempfile.TemporaryDirectory(prefix="cardak-codex-") as work:
            out = Path(work) / "last-message.txt"
            argv = ["codex", "exec", "--ephemeral", "--sandbox", "read-only"]
            argv += ["--skip-git-repo-check", "--ignore-rules", "--json", "-o", str(out)]
            argv += ["-m", self.model] if self.model else []
            argv.append("-")
            try:
                done = self.run(
                    argv,
                    input=f"{system}\n{ANSWER_ONLY}\n\n{user}",
                    cwd=work,
                    capture_output=True,
                    text=True,
                    timeout=TIMEOUT,
                    check=False,
                )
            except FileNotFoundError:
                raise ProviderError("codex isn't installed or isn't on PATH") from None
            except subprocess.TimeoutExpired:
                raise ProviderError(f"codex timed out after {TIMEOUT:.0f} s") from None
            text = out.read_text(encoding="utf-8").strip() if out.exists() else None
            if done.returncode != 0 or text is None:
                why = " ".join((done.stderr or "").split())[:MAX_ERROR]
                raise ProviderError(f"codex exited {done.returncode}: {why}")
        tokens_in, tokens_out = _usage(done.stdout or "")
        return Reply(text, self.model or "codex", "end_turn", tokens_in, tokens_out)


def codex_version(run: Callable[..., Any] = subprocess.run) -> str | None:
    """`codex --version`, or None when codex can't be run."""
    try:
        done = run(["codex", "--version"], capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout.strip() or None if done.returncode == 0 else None
