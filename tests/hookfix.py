"""Recorded Claude Code hook payloads (tests/fixtures/hooks)."""

import json
from pathlib import Path
from typing import Any

HOOKS = Path(__file__).parent / "fixtures" / "hooks"


def payload(name: str) -> dict[str, Any]:
    return json.loads((HOOKS / f"{name}.json").read_text())["payload"]


def text(name: str, **overrides: Any) -> str:
    return json.dumps(payload(name) | overrides)
