"""JSON over HTTP for the summary providers, with errors worded for the deck."""

import json
import urllib.error
import urllib.request
from typing import Any

MAX_ERROR_BODY = 300


class ProviderError(Exception):
    pass


def _send(request: urllib.request.Request, timeout: float) -> Any:
    url = request.full_url
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", "replace").strip()[:MAX_ERROR_BODY]
        raise ProviderError(f"HTTP {e.code}: {text or e.reason}") from None
    except (urllib.error.URLError, OSError) as e:
        reason = getattr(e, "reason", e)
        raise ProviderError(f"can't reach {url}: {reason}") from None
    try:
        return json.loads(body)
    except ValueError:
        raise ProviderError(f"{url} answered, but not JSON") from None


def post_json(url: str, body: dict[str, Any], headers: dict[str, str], timeout: float) -> Any:
    data = json.dumps(body).encode()
    all_headers = {"Content-Type": "application/json", **headers}
    return _send(urllib.request.Request(url, data, all_headers, method="POST"), timeout)


def get_json(url: str, timeout: float) -> Any:
    return _send(urllib.request.Request(url), timeout)
