"""Claude Code transcripts as usage: one row per API reply, one per prompt the user typed.

Every `*.jsonl` under `~/.claude/projects` (subagents in files below their session) is read
from a byte offset to its last complete line, so a pass only reads what was appended. A
reply is written once per content block, each line repeating its `message.id`, `requestId`
and usage: the key is both ids. Not a stable API, so anything unexpected is skipped.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

READ_LIMIT = 4 * 2**20  # bytes per read: a month's biggest transcript is ~80 MB


@dataclass(frozen=True)
class Reply:
    key: str  # "<message.id>:<requestId>"
    ts: float
    session_id: str | None
    cwd: str | None
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_5m_tokens: int
    cache_write_1h_tokens: int
    speed: str | None  # "standard", "fast" (fast mode), or absent
    sidechain: bool = False  # a subagent's reply, filed under its parent's session (#86)


@dataclass(frozen=True)
class Prompt:
    key: str  # the line's uuid
    ts: float
    session_id: str | None
    cwd: str | None
    chars: int  # text only; images and tool results don't count


@dataclass(frozen=True)
class Usage:
    replies: tuple[Reply, ...] = ()
    prompts: tuple[Prompt, ...] = ()


@dataclass(frozen=True)
class TranscriptFile:
    path: Path
    size: int
    inode: int
    mtime: float


def default_root() -> Path:
    return Path.home() / ".claude" / "projects"


def transcript_files(root: Path, since: float) -> list[TranscriptFile]:
    """Every transcript under root, at any depth, modified at or after `since`."""
    found = []
    for path in root.rglob("*.jsonl"):
        try:
            st = path.stat()
        except OSError:
            continue
        if st.st_mtime >= since:
            found.append(TranscriptFile(path, st.st_size, st.st_ino, st.st_mtime))
    return found


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _ts(record: dict) -> float | None:
    try:
        return datetime.fromisoformat(record["timestamp"]).timestamp()
    except (KeyError, TypeError, ValueError):
        return None


def _reply(record: dict, ts: float) -> Reply | None:
    message = record.get("message")
    if not isinstance(message, dict) or not isinstance(message.get("usage"), dict):
        return None
    usage, model = message["usage"], _str(message.get("model"))
    mid, req = _str(message.get("id")), _str(record.get("requestId"))
    if mid is None or req is None or model is None or model == "<synthetic>":
        return None
    split = usage.get("cache_creation")
    if isinstance(split, dict):
        write_5m = _int(split.get("ephemeral_5m_input_tokens"))
        write_1h = _int(split.get("ephemeral_1h_input_tokens"))
    else:  # no breakdown: all at the 5-minute rate
        write_5m, write_1h = _int(usage.get("cache_creation_input_tokens")), 0
    return Reply(
        key=f"{mid}:{req}",
        ts=ts,
        session_id=_str(record.get("sessionId")),
        cwd=_str(record.get("cwd")),
        model=model,
        input_tokens=_int(usage.get("input_tokens")),
        output_tokens=_int(usage.get("output_tokens")),
        cache_read_tokens=_int(usage.get("cache_read_input_tokens")),
        cache_write_5m_tokens=write_5m,
        cache_write_1h_tokens=write_1h,
        speed=_str(usage.get("speed")),
        sidechain=record.get("isSidechain") is True,
    )


def _chars(content: object) -> int | None:
    """Length of the text the user typed; None for a line that only carries tool results."""
    if isinstance(content, str):
        return len(content)
    if not isinstance(content, list):
        return None
    texts = [b["text"] for b in content if isinstance(b, dict) and b.get("type") == "text"]
    texts = [t for t in texts if isinstance(t, str)]
    return sum(map(len, texts)) if texts else None


def _prompt(record: dict, ts: float) -> Prompt | None:
    if record.get("isMeta") or record.get("isSidechain"):
        return None  # cardak's reminders and the like; Claude prompting a subagent
    message, key = record.get("message"), _str(record.get("uuid"))
    chars = _chars(message.get("content")) if isinstance(message, dict) else None
    if key is None or chars is None:
        return None
    return Prompt(key, ts, _str(record.get("sessionId")), _str(record.get("cwd")), chars)


def _record(line: bytes) -> Reply | Prompt | None:
    if b'"usage"' not in line and b'"user"' not in line:
        return None  # most lines are neither; json.loads is the expensive part
    try:
        record = json.loads(line)
    except ValueError:
        return None
    if not isinstance(record, dict) or (ts := _ts(record)) is None:
        return None
    if record.get("type") == "assistant":
        return _reply(record, ts)
    return _prompt(record, ts) if record.get("type") == "user" else None


def _complete_lines(path: Path, offset: int, limit: int) -> bytes:
    """Up to `limit` bytes from offset, cut after the last newline; a longer line is read whole.
    Raises OSError."""
    with path.open("rb") as f:
        f.seek(offset)
        data = f.read(limit)
        while b"\n" not in data and len(data) % limit == 0 and data:
            more = f.read(limit)  # one line longer than limit: read on to its end
            if not more:
                break
            data += more
    return data[: data.rfind(b"\n") + 1]


def read_usage(path: Path, offset: int, limit: int = READ_LIMIT) -> tuple[Usage, int]:
    """Replies and prompts in at most `limit` bytes from `offset`, cut at the last complete
    line, and where that line ends: call again from there until it stops moving."""
    try:
        data = _complete_lines(path, offset, limit)
    except OSError:
        return Usage(), offset
    end = len(data)
    replies: dict[str, Reply] = {}
    prompts: list[Prompt] = []
    for line in data.splitlines():
        found = _record(line)
        if isinstance(found, Reply):
            replies.setdefault(found.key, found)
        elif found is not None:
            prompts.append(found)
    return Usage(tuple(replies.values()), tuple(prompts)), offset + end
