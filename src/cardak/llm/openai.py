"""Waiting summaries from any OpenAI-compatible API: Fireworks, OpenAI, llama.cpp, vLLM (#48)."""

from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from .anthropic import MAX_TOKENS, Reply
from .http import ProviderError, post_json

TIMEOUT = 20.0


class OpenAISummarizer:
    def __init__(
        self, url: str, key: str | None, model: str, post: Callable[..., Any] = post_json
    ) -> None:
        self.url = url.rstrip("/")
        self.key = key
        self.model = model
        self.post = post

    def _is_openai(self) -> bool:
        return urlsplit(self.url).hostname == "api.openai.com"

    def summarize(self, system: str, user: str) -> Reply:
        # OpenAI's own API refuses max_tokens for current models; compatible servers want it
        limit = "max_completion_tokens" if self._is_openai() else "max_tokens"
        body = {
            "model": self.model,
            limit: MAX_TOKENS,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        headers = {"Authorization": f"Bearer {self.key}"} if self.key else {}
        data = self.post(f"{self.url}/chat/completions", body, headers, TIMEOUT)
        try:
            choice = data["choices"][0]
            text = choice["message"].get("content")
            reason = choice.get("finish_reason")
            usage = data.get("usage") or {}
            return Reply(
                text=text if isinstance(text, str) else None,
                model=str(data.get("model") or self.model),
                stop_reason="end_turn" if reason == "stop" else reason,
                input_tokens=int(usage.get("prompt_tokens") or 0),
                output_tokens=int(usage.get("completion_tokens") or 0),
            )
        except (KeyError, IndexError, TypeError, ValueError, AttributeError):
            raise ProviderError(f"unexpected reply from {self.url}") from None
