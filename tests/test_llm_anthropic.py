from collections.abc import Sequence
from types import SimpleNamespace

import pytest

from cardak.llm.anthropic import AnthropicSummarizer, Reply

MODEL = "claude-haiku-5-5"


class FakeMessages:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def create(self, **kw: object) -> object:
        self.calls.append(kw)
        return self.response


class FakeClient:
    def __init__(self, response: object) -> None:
        self.messages = FakeMessages(response)


def response(
    blocks: Sequence[object] | None = None,
    stop_reason: str = "end_turn",
    usage: tuple[int, int] = (1500, 30),
) -> SimpleNamespace:
    if blocks is None:
        blocks = [SimpleNamespace(type="text", text="Did x; needs y")]
    return SimpleNamespace(
        content=blocks,
        model=MODEL,
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=usage[0], output_tokens=usage[1]),
    )


def test_request_parameters() -> None:
    fake = FakeClient(response())
    AnthropicSummarizer("k", MODEL, client=fake).summarize("SYS", "USER")
    [kw] = fake.messages.calls
    assert kw["model"] == MODEL
    assert kw["max_tokens"] == 256
    assert kw["thinking"] == {"type": "disabled"}
    assert kw["output_config"] == {"effort": "low"}
    assert kw["system"] == "SYS"
    assert kw["messages"] == [{"role": "user", "content": "USER"}]


def test_reply_fields() -> None:
    fake = FakeClient(response(stop_reason="end_turn", usage=(1500, 30)))
    reply = AnthropicSummarizer("k", MODEL, client=fake).summarize("s", "u")
    assert reply == Reply("Did x; needs y", MODEL, "end_turn", 1500, 30)


def test_first_text_block_wins() -> None:
    blocks = [
        SimpleNamespace(type="thinking", thinking=""),
        SimpleNamespace(type="text", text="A"),
        SimpleNamespace(type="text", text="B"),
    ]
    fake = FakeClient(response(blocks))
    assert AnthropicSummarizer("k", MODEL, client=fake).summarize("s", "u").text == "A"


def test_no_text_block_is_none() -> None:
    fake = FakeClient(response([], stop_reason="refusal"))
    reply = AnthropicSummarizer("k", MODEL, client=fake).summarize("s", "u")
    assert reply.text is None and reply.stop_reason == "refusal"


def test_real_client_gets_the_key_explicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-env")
    client = AnthropicSummarizer("from-file", MODEL).client
    assert client.api_key == "from-file"
    assert client.max_retries == 2
    assert client.timeout == 20.0
