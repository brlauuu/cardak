import json
import re
import subprocess
import urllib.error
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from cardak.llm import codex, http, ollama, openai
from cardak.llm.codex import CodexSummarizer, codex_version
from cardak.llm.http import ProviderError, get_json, post_json
from cardak.llm.ollama import OllamaSummarizer, list_models
from cardak.llm.openai import OpenAISummarizer

FIXTURES = Path(__file__).parent / "fixtures" / "llm"
LINE = "Wants to delete build/; approve the rm -rf"


def _fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


class FakePost:
    def __init__(self, reply: Any) -> None:
        self.reply = reply
        self.calls: list[tuple[str, dict[str, Any], dict[str, str], float]] = []

    def __call__(
        self, url: str, body: dict[str, Any], headers: dict[str, str], timeout: float
    ) -> Any:
        self.calls.append((url, body, headers, timeout))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def test_ollama_parses_reply() -> None:
    reply = OllamaSummarizer(
        "http://h:1", "gemma4:e4b", post=FakePost(_fixture("ollama_chat.json"))
    )
    got = reply.summarize("sys", "user")
    assert (got.text, got.model, got.stop_reason) == (LINE, "gemma4:e4b", "end_turn")
    assert (got.input_tokens, got.output_tokens) == (46, 9)


def test_ollama_sends_system_model_and_no_thinking() -> None:
    post = FakePost(_fixture("ollama_chat.json"))
    OllamaSummarizer("http://h:1/", "gemma4:e4b", post=post).summarize("sys", "user")
    url, body, _headers, timeout = post.calls[0]
    assert url == "http://h:1/api/chat" and timeout == ollama.TIMEOUT == 60.0
    assert body["model"] == "gemma4:e4b" and body["stream"] is False
    assert body["think"] is False  # a thinking model spent 80 s and answered nothing (measured)
    assert body["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "user"},
    ]


def test_ollama_cut_off_reply_is_not_end_turn() -> None:
    data = _fixture("ollama_chat.json") | {"done_reason": "length"}
    got = OllamaSummarizer("http://h", "m", post=FakePost(data)).summarize("s", "u")
    assert got.stop_reason == "length"


def test_ollama_http_error_raises_readable() -> None:
    post = FakePost(ProviderError('HTTP 404: {"error":"model \'m\' not found"}'))
    with pytest.raises(ProviderError, match="HTTP 404"):
        OllamaSummarizer("http://h", "m", post=post).summarize("s", "u")


def test_ollama_odd_reply_raises_readable() -> None:
    with pytest.raises(ProviderError, match="unexpected reply"):
        OllamaSummarizer("http://h", "m", post=FakePost({"x": 1})).summarize("s", "u")


def test_list_models() -> None:
    def get(url: str, timeout: float) -> Any:
        assert url == "http://h/api/tags"
        return _fixture("ollama_tags.json")

    assert list_models("http://h", get=get) == ["gemma4:e4b", "qwen2.5:3b"]


def test_openai_parses_reply_and_sends_bearer() -> None:
    post = FakePost(_fixture("openai_chat.json"))
    got = OpenAISummarizer("https://api.example/v1/", "sk-1", "qwen2.5:3b", post=post).summarize(
        "sys", "user"
    )
    assert (got.text, got.stop_reason, got.input_tokens, got.output_tokens) == (
        LINE,
        "end_turn",
        45,
        21,
    )
    url, body, headers, timeout = post.calls[0]
    assert url == "https://api.example/v1/chat/completions" and timeout == openai.TIMEOUT == 20.0
    assert headers["Authorization"] == "Bearer sk-1"
    assert body["model"] == "qwen2.5:3b" and body["messages"][0] == {
        "role": "system",
        "content": "sys",
    }


def test_openai_without_key_sends_no_authorization() -> None:
    post = FakePost(_fixture("openai_chat.json"))
    OpenAISummarizer("http://127.0.0.1:8080/v1", None, "m", post=post).summarize("s", "u")
    assert "Authorization" not in post.calls[0][2]


def test_openai_length_finish_is_not_end_turn() -> None:
    data = _fixture("openai_chat.json")
    data["choices"][0]["finish_reason"] = "length"
    got = OpenAISummarizer("http://h", None, "m", post=FakePost(data)).summarize("s", "u")
    assert got.stop_reason == "length"


def test_openai_odd_reply_raises_readable() -> None:
    with pytest.raises(ProviderError, match="unexpected reply"):
        OpenAISummarizer("http://h", None, "m", post=FakePost({"choices": []})).summarize("s", "u")


class FakeRun:
    def __init__(self, write: str | None = LINE, code: int = 0, stderr: str = "") -> None:
        self.write, self.code, self.stderr = write, code, stderr
        self.calls: list[tuple[Sequence[str], dict[str, Any]]] = []

    def __call__(self, argv: Sequence[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append((argv, kw))
        if self.write is not None:
            out = Path(argv[argv.index("-o") + 1])
            out.write_text(self.write + "\n")
        stdout = (FIXTURES / "codex_exec.jsonl").read_text()
        return subprocess.CompletedProcess(argv, self.code, stdout, self.stderr)


def test_codex_builds_command() -> None:
    run = FakeRun()
    CodexSummarizer("gpt-5.4-mini", run=run).summarize("sys", "user")
    argv, kw = run.calls[0]
    assert argv[:2] == ["codex", "exec"]
    for flag in ("--ephemeral", "--skip-git-repo-check", "--ignore-rules", "--json"):
        assert flag in argv
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert argv[argv.index("-m") + 1] == "gpt-5.4-mini" and argv[-1] == "-"
    assert kw["input"].startswith("sys") and kw["input"].endswith("user")
    assert kw["timeout"] == codex.TIMEOUT == 60.0
    assert not Path(kw["cwd"]).exists()  # its empty temp dir is gone afterwards


def test_codex_without_model_passes_no_m() -> None:
    run = FakeRun()
    CodexSummarizer(None, run=run).summarize("s", "u")
    assert "-m" not in run.calls[0][0]


def test_codex_reads_last_message() -> None:
    got = CodexSummarizer(None, run=FakeRun()).summarize("s", "u")
    assert (got.text, got.stop_reason, got.model) == (LINE, "end_turn", "codex")
    assert (got.input_tokens, got.output_tokens) == (26272, 50)


def test_codex_failure_is_readable() -> None:
    run = FakeRun(write=None, code=1, stderr="Error: not logged in\n")
    with pytest.raises(ProviderError, match="codex exited 1: Error: not logged in"):
        CodexSummarizer(None, run=run).summarize("s", "u")


def test_codex_timeout() -> None:
    def run(argv: Sequence[str], **kw: Any) -> Any:
        raise subprocess.TimeoutExpired(argv, kw["timeout"])

    with pytest.raises(ProviderError, match="codex timed out after 60 s"):
        CodexSummarizer(None, run=run).summarize("s", "u")


def test_codex_missing() -> None:
    def run(argv: Sequence[str], **kw: Any) -> Any:
        raise FileNotFoundError(argv[0])

    with pytest.raises(ProviderError, match="codex isn't installed"):
        CodexSummarizer(None, run=run).summarize("s", "u")
    assert codex_version(run=run) is None


def test_codex_version() -> None:
    def run(argv: Sequence[str], **kw: Any) -> Any:
        assert list(argv) == ["codex", "--version"]
        return subprocess.CompletedProcess(argv, 0, "codex-cli 0.160.0\n", "")

    assert codex_version(run=run) == "codex-cli 0.160.0"


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *a: object) -> None:
        pass

    def read(self) -> bytes:
        return self.body


def test_post_json_sends_json(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []

    def urlopen(request: Any, timeout: float) -> FakeResponse:
        seen.append((request, timeout))
        return FakeResponse(b'{"ok": 1}')

    monkeypatch.setattr(http.urllib.request, "urlopen", urlopen)
    assert post_json("http://h/x", {"a": 1}, {"Authorization": "Bearer k"}, 5.0) == {"ok": 1}
    request, timeout = seen[0]
    assert json.loads(request.data) == {"a": 1} and timeout == 5.0
    assert request.get_header("Content-type") == "application/json"
    assert request.get_header("Authorization") == "Bearer k"


def test_http_errors_are_readable(monkeypatch: pytest.MonkeyPatch) -> None:
    import io

    def refused(request: Any, timeout: float) -> Any:
        raise urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))

    monkeypatch.setattr(http.urllib.request, "urlopen", refused)
    with pytest.raises(ProviderError, match="can't reach http://h/x"):
        get_json("http://h/x", 1.0)

    def status(request: Any, timeout: float) -> Any:
        body = io.BytesIO(b'{"error": "bad key"}')
        raise urllib.error.HTTPError("http://h/x", 401, "Unauthorized", {}, body)  # type: ignore[arg-type]

    monkeypatch.setattr(http.urllib.request, "urlopen", status)
    with pytest.raises(ProviderError, match='HTTP 401: {"error": "bad key"}'):
        post_json("http://h/x", {}, {}, 1.0)

    monkeypatch.setattr(http.urllib.request, "urlopen", lambda r, timeout: FakeResponse(b"<html"))
    with pytest.raises(ProviderError, match="not JSON"):
        get_json("http://h/x", 1.0)


def test_llm_imports_nothing_from_cardak() -> None:
    root = Path(__file__).parent.parent / "src" / "cardak" / "llm"
    for source in root.glob("*.py"):
        found = re.search(r"^\s*(from|import) cardak\b", source.read_text(), re.MULTILINE)
        assert found is None, source.name


def test_openai_api_gets_max_completion_tokens() -> None:
    post = FakePost(_fixture("openai_chat.json"))
    OpenAISummarizer("https://api.openai.com/v1", "sk", "gpt-5-mini", post=post).summarize("s", "u")
    body = post.calls[0][1]
    assert "max_tokens" not in body and body["max_completion_tokens"] == 256
    other = FakePost(_fixture("openai_chat.json"))
    OpenAISummarizer("http://127.0.0.1:8080/v1", None, "m", post=other).summarize("s", "u")
    assert other.calls[0][1]["max_tokens"] == 256


def test_codex_failure_reads_the_json_error() -> None:
    failed = {"type": "turn.failed", "error": {"message": "usage limit reached"}}
    events = '{"type":"thread.started"}\n' + json.dumps(failed) + "\n"

    def run(argv: Sequence[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 1, events, "")

    with pytest.raises(ProviderError, match="codex exited 1: usage limit reached"):
        CodexSummarizer(None, run=run).summarize("s", "u")


def test_codex_timeout_kills_its_children(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os
    import time

    pidfile = tmp_path / "child.pid"
    fake = tmp_path / "fake-codex"
    fake.write_text(f"#!/bin/sh\nsleep 30 &\necho $! > {pidfile}\nwait\n")
    fake.chmod(0o755)
    with pytest.raises(subprocess.TimeoutExpired):
        codex.run_in_group([str(fake)], input="", cwd=str(tmp_path), timeout=1.0)
    child = int(pidfile.read_text())
    for _ in range(50):
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail("the timed-out codex left its child running")
