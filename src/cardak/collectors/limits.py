"""Plan limits from the JSON Claude Code pipes to the status line command (#70).

`rate_limits.five_hour` and `rate_limits.seven_day` each carry `used_percentage` (0–100) and
`resets_at` (Unix seconds); the object exists only for Pro/Max plans, after a session's first
reply, and either window may be missing (code.claude.com/docs/en/statusline.md).
"""

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class Window:
    used: float  # percent of the limit used
    resets_at: float


@dataclass(frozen=True)
class Reading:
    five_hour: Window | None
    seven_day: Window | None


def _number(v: object) -> float | None:
    if isinstance(v, bool) or not isinstance(v, int | float):
        return None
    return float(v)


def _window(raw: object) -> Window | None:
    if not isinstance(raw, dict):
        return None
    used, resets = _number(raw.get("used_percentage")), _number(raw.get("resets_at"))
    if used is None or resets is None or used < 0:
        return None
    return Window(min(used, 100.0), resets)


def parse_statusline(data: bytes) -> Reading | None:
    """The reading in one status line input; None when it carries no valid window."""
    try:
        record = json.loads(data)
    except ValueError:
        return None
    limits = record.get("rate_limits") if isinstance(record, dict) else None
    if not isinstance(limits, dict):
        return None
    reading = Reading(_window(limits.get("five_hour")), _window(limits.get("seven_day")))
    return reading if reading.five_hour or reading.seven_day else None
