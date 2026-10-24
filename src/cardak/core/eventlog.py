"""The event log as the deck lists it: newest first, quiet repeats folded in. Pure."""

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from cardak.core.store import ResourceEvent

KIND_LABELS = {
    "memory": "memory",
    "big": "big",
    "growth": "growing",
    "busy": "busy",
    "machine_busy": "machine busy",
    "crash": "WezTerm closed",
    "daemon": "daemon",
}
_SENTENCE_END = re.compile(r"(?<=\.) ")


@dataclass(frozen=True)
class LogLine:
    ts: float
    level: str
    kind: str
    subject: str
    text: str  # one line: a crash report's first sentence
    full: str | None  # the whole crash report, when it has more than one sentence
    repeats: int  # quiet repeats folded into this notified alert
    until: float | None  # the last repeat's time


def sentences(text: str) -> list[str]:
    return _SENTENCE_END.split(text)


def _line(e: ResourceEvent) -> LogLine:
    if e.kind == "crash" and len(parts := sentences(e.text)) > 1:
        return LogLine(e.ts, e.level, e.kind, e.subject, parts[0], e.text, 0, None)
    return LogLine(e.ts, e.level, e.kind, e.subject, e.text, None, 0, None)


def log_lines(events: Sequence[ResourceEvent]) -> list[LogLine]:
    """Newest first. A quiet repeat (logged, not notified) folds into its notified line."""
    lines: list[LogLine] = []
    open_alert: dict[tuple[str, str], int] = {}  # (subject, kind) → index of its notified line
    for e in sorted(events, key=lambda e: e.ts):
        key = (e.subject, e.kind)
        index = open_alert.get(key)
        if not e.notified and index is not None and e.kind not in ("crash", "daemon"):
            line = lines[index]
            lines[index] = replace(line, repeats=line.repeats + 1, until=e.ts)
            continue
        lines.append(_line(e))
        if e.notified:
            open_alert[key] = len(lines) - 1
    return lines[::-1]


def line_text(line: LogLine) -> str:
    when = datetime.fromtimestamp(line.ts).strftime("%b %d %H:%M")
    text = f"{when}  {KIND_LABELS.get(line.kind, line.kind)}  {line.text}"
    if line.repeats and line.until is not None:
        until = datetime.fromtimestamp(line.until).strftime("%H:%M")
        text += f"  (repeated {line.repeats}× until {until})"
    return text
