"""The handoff note `/checkpoint` appends to: `<dir>/.cardak/handoff.md`. Read-only here.

Only a `## YYYY-MM-DD HH:MM[ · branch]` heading outside a code fence starts an entry; any other
line, `## Notes` included, belongs to the entry above it. The preamble is not an entry.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

NOTE = Path(".cardak") / "handoff.md"
_HEADING = re.compile(r"^## (\d{4}-\d{2}-\d{2} \d{2}:\d{2})(?: · (.+?))?\s*$")


@dataclass(frozen=True)
class Entry:
    when: datetime  # local time, naive
    branch: str | None
    body: str


def _heading(line: str) -> tuple[datetime, str | None] | None:
    m = _HEADING.match(line)
    if m is None:
        return None
    try:
        when = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return when, m.group(2)


def parse_entries(text: str) -> list[Entry]:
    """Entries in file order (oldest first, as the file is appended to)."""
    out: list[Entry] = []
    head: tuple[datetime, str | None] | None = None
    body: list[str] = []
    fenced = False
    for line in text.removeprefix("﻿").splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
        found = None if fenced else _heading(line)
        if found is None:
            body.append(line)
            continue
        if head is not None:
            out.append(Entry(head[0], head[1], "\n".join(body).strip()))
        head, body = found, []
    if head is not None:
        out.append(Entry(head[0], head[1], "\n".join(body).strip()))
    return out


def read_note(top: Path) -> list[Entry] | None:
    """The entries of `top`'s note; None when there is none or it can't be read."""
    try:
        text = (top / NOTE).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return parse_entries(text)
