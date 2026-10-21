"""Waiting summaries through Anthropic's API, with the key from cardak's own key file."""

from dataclasses import dataclass
from typing import Any

MAX_TOKENS = 256
TIMEOUT = 20.0
MAX_RETRIES = 2


@dataclass(frozen=True)
class Reply:
    text: str | None  # the first text block; None when there is none (a refusal, say)
    model: str
    stop_reason: str | None
    input_tokens: int
    output_tokens: int


class AnthropicSummarizer:
    def __init__(self, api_key: str, model: str, client: Any = None) -> None:
        if client is None:
            import anthropic  # slow to import; only the background job gets here

            # api_key given explicitly, so ANTHROPIC_API_KEY or an `ant` profile can't take over
            client = anthropic.Anthropic(api_key=api_key, timeout=TIMEOUT, max_retries=MAX_RETRIES)
        self.client = client
        self.model = model

    def summarize(self, system: str, user: str) -> Reply:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=MAX_TOKENS,
            thinking={"type": "disabled"},  # a one-line summary needs no reasoning
            output_config={"effort": "low"},
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = next((b.text for b in response.content if b.type == "text"), None)
        return Reply(
            text=text,
            model=response.model,
            stop_reason=response.stop_reason,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
