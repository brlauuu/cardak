"""Waiting summaries from a local Ollama server (#47)."""

from collections.abc import Callable
from typing import Any

from .anthropic import MAX_TOKENS, Reply
from .http import ProviderError, get_json, post_json

TIMEOUT = 60.0  # a 4B model on CPU, cold
LIST_TIMEOUT = 5.0


class OllamaSummarizer:
    def __init__(self, url: str, model: str, post: Callable[..., Any] = post_json) -> None:
        self.url = url.rstrip("/")
        self.model = model
        self.post = post

    def summarize(self, system: str, user: str) -> Reply:
        body = {
            "model": self.model,
            "stream": False,
            "think": False,  # a thinking model spent its whole budget thinking and said nothing
            "options": {"num_predict": MAX_TOKENS},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        data = self.post(f"{self.url}/api/chat", body, {}, TIMEOUT)
        try:
            text = data["message"]["content"]
            reason = data.get("done_reason")
            return Reply(
                text=text if isinstance(text, str) else None,
                model=str(data.get("model") or self.model),
                stop_reason="end_turn" if reason == "stop" else reason,
                input_tokens=int(data.get("prompt_eval_count") or 0),
                output_tokens=int(data.get("eval_count") or 0),
            )
        except (KeyError, TypeError, ValueError, AttributeError):
            raise ProviderError(f"unexpected reply from {self.url}") from None


def list_models(url: str, get: Callable[[str, float], Any] = get_json) -> list[str]:
    data = get(f"{url.rstrip('/')}/api/tags", LIST_TIMEOUT)
    try:
        return [m["name"] for m in data["models"] if isinstance(m.get("name"), str)]
    except (KeyError, TypeError, AttributeError):
        raise ProviderError(f"unexpected reply from {url}") from None
