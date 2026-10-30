"""Waiting summaries from the Codex CLI, on the user's Codex plan (#49).

`codex exec` runs a whole agent; it gets an empty temporary directory, a read-only sandbox, no
saved session and no execpolicy rules, and is told to answer without tools.
"""

import contextlib
import json
import os
import signal
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from .anthropic import Reply
from .http import ProviderError

TIMEOUT = 60.0
MAX_ERROR = 300
ANSWER_ONLY = "Answer with the line only. Do not run commands or use tools."


def run_in_group(
    argv: Sequence[str], *, input: str, cwd: str, timeout: float
) -> subprocess.CompletedProcess[str]:
    """subprocess.run in a session of its own: on a timeout the whole group is killed.

    `codex` is a Node wrapper that spawns the native binary; killing only the wrapper would
    leave the agent running on the user's plan.
    """
    with subprocess.Popen(
        argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=cwd, text=True, start_new_session=True,
    ) as proc:  # fmt: skip
        try:
            stdout, stderr = proc.communicate(input, timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
            raise
        return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)


def _events(stdout: str) -> list[dict[str, Any]]:
    found = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            found.append(event)
    return found


def _failure(stdout: str) -> str | None:
    """What codex said went wrong, in its --json events: a failed turn or a fatal error."""
    for event in reversed(_events(stdout)):
        error = event.get("error")
        if event.get("type") == "turn.failed" and isinstance(error, dict):
            return str(error.get("message") or "")
        if event.get("type") == "error":
            return str(event.get("message") or "")
    return None


def _usage(stdout: str) -> tuple[int, int]:
    """Tokens from the last `turn.completed` event of `--json` output; 0 when there is none."""
    found = (0, 0)
    for event in _events(stdout):
        usage = event.get("usage")
        if event.get("type") == "turn.completed" and isinstance(usage, dict):
            with contextlib.suppress(TypeError, ValueError):  # odd counts: keep the last good ones
                found = (int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0))
    return found


class CodexSummarizer:
    def __init__(self, model: str | None, run: Callable[..., Any] = run_in_group) -> None:
        self.model = model
        self.run = run

    def _argv(self, out: Path) -> list[str]:
        argv = ["codex", "exec", "--ephemeral", "--sandbox", "read-only"]
        argv += ["--skip-git-repo-check", "--ignore-rules", "--json", "-o", str(out)]
        argv += ["-m", self.model] if self.model else []
        return [*argv, "-"]

    def _exec(self, out: Path, prompt: str) -> Any:
        try:
            return self.run(self._argv(out), input=prompt, cwd=str(out.parent), timeout=TIMEOUT)
        except FileNotFoundError:
            raise ProviderError("codex isn't installed or isn't on PATH") from None
        except subprocess.TimeoutExpired:
            raise ProviderError(f"codex timed out after {TIMEOUT:.0f} s") from None

    def summarize(self, system: str, user: str) -> Reply:
        with tempfile.TemporaryDirectory(prefix="cardak-codex-") as work:
            out = Path(work) / "last-message.txt"
            done = self._exec(out, f"{system}\n{ANSWER_ONLY}\n\n{user}")
            text = out.read_text(encoding="utf-8").strip() if out.exists() else None
        if done.returncode != 0 or text is None:
            why = " ".join((done.stderr or _failure(done.stdout or "") or "").split())
            raise ProviderError(f"codex exited {done.returncode}: {why[:MAX_ERROR]}")
        tokens_in, tokens_out = _usage(done.stdout or "")
        return Reply(text, self.model or "codex", "end_turn", tokens_in, tokens_out)


def codex_version(run: Callable[..., Any] = subprocess.run) -> str | None:
    """`codex --version`, or None when codex can't be run."""
    try:
        done = run(["codex", "--version"], capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout.strip() or None if done.returncode == 0 else None
