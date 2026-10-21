"""Claude Code transcripts (`transcript_path` in hook payloads): the messages of a session.

JSONL, one record per line. Most record types are bookkeeping (attachments, API requests,
titles, snapshots); only user and assistant messages matter here. Not a stable API, so
anything unexpected is skipped, never raised.
"""

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

EntryKind = Literal["user", "assistant", "tool", "thinking"]


@dataclass(frozen=True)
class Entry:
    kind: EntryKind
    text: str = ""  # user/assistant/thinking text; a tool's result
    tool_name: str | None = None
    tool_input: Mapping[str, object] = field(default_factory=dict)


def _records(path: Path) -> Iterator[dict]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and not record.get("isSidechain") and not record.get("isMeta"):
            yield record


def _blocks(content: object) -> list[dict]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _result_text(content: object) -> str:
    return "\n".join(b.get("text", "") for b in _blocks(content) if b.get("type") == "text")


def read_transcript(path: Path, limit: int = 200) -> tuple[list[Entry], int]:
    """The last `limit` entries, and how many came before them."""
    entries: list[Entry] = []
    tools: dict[str, int] = {}  # tool_use id -> index in entries, to attach its result
    for record in _records(path):
        kind, message = record.get("type"), record.get("message")
        if kind not in ("user", "assistant") or not isinstance(message, dict):
            continue
        for block in _blocks(message.get("content")):
            match block.get("type"):
                case "text" if isinstance(block.get("text"), str) and block["text"].strip():
                    entries.append(Entry(kind, block["text"]))
                case "thinking" if isinstance(block.get("thinking"), str):
                    entries.append(Entry("thinking", block["thinking"]))
                case "tool_use" if isinstance(block.get("name"), str):
                    tool_input = block.get("input")
                    tools[str(block.get("id"))] = len(entries)
                    entries.append(
                        Entry(
                            "tool",
                            tool_name=block["name"],
                            tool_input=tool_input if isinstance(tool_input, dict) else {},
                        )
                    )
                case "tool_result" if str(block.get("tool_use_id")) in tools:
                    i = tools[str(block["tool_use_id"])]
                    entries[i] = replace(entries[i], text=_result_text(block.get("content")))
    earlier = max(0, len(entries) - limit)
    return entries[earlier:], earlier
