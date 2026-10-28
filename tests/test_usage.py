import os
import shutil
from datetime import datetime
from pathlib import Path

from cardak.collectors.usage import Prompt, Reply, read_usage, transcript_files

FIXTURES = Path(__file__).parent / "fixtures" / "transcripts" / "usage"
SESSION = FIXTURES / "session.jsonl"
SESSION_ID = "00000000-0000-4000-8000-0000000000a1"


def _ts(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp()


def test_read_replies_one_per_reply() -> None:
    usage, offset = read_usage(SESSION, 0)
    assert [r.key for r in usage.replies] == ["msg_u1:req_u1", "msg_u2:req_u2"]
    assert usage.replies[0] == Reply(
        key="msg_u1:req_u1",
        ts=_ts("2026-10-20T10:00:05+00:00"),
        session_id=SESSION_ID,
        cwd="/home/user/webshop",
        model="claude-opus-5-5",
        input_tokens=3,
        output_tokens=400,
        cache_read_tokens=20000,
        cache_write_5m_tokens=0,
        cache_write_1h_tokens=5000,
        speed="standard",
    )
    assert offset == SESSION.stat().st_size


def test_read_replies_splits_cache_writes() -> None:
    usage, _ = read_usage(SESSION, 0)
    first, second = usage.replies
    assert (first.cache_write_5m_tokens, first.cache_write_1h_tokens) == (0, 5000)
    # no `cache_creation` breakdown: all of cache_creation_input_tokens at the 5-minute rate
    assert (second.cache_write_5m_tokens, second.cache_write_1h_tokens) == (300, 0)
    assert second.speed is None


def test_read_replies_skips_synthetic_and_garbage(tmp_path: Path) -> None:
    usage, _ = read_usage(SESSION, 0)
    assert all(r.model != "<synthetic>" for r in usage.replies)
    odd = tmp_path / "odd.jsonl"
    t = '"timestamp":"2026-10-20T10:00:00Z"'
    odd.write_text(
        f'{{"type":"assistant",{t},"requestId":"r","message":{{"model":"m","usage":{{}}}}}}\n'
        f'{{"type":"assistant",{t},"requestId":"r","message":{{"id":"x","usage":"nope"}}}}\n'
        f'{{"type":"assistant","requestId":"r","message":{{"id":"x","model":"m","usage":{{}}}}}}\n'
        '[1, "usage"]\n'
        f'{{"type":"user",{t},"message":{{"role":"user","content":42}},"uuid":"u"}}\n'
    )
    assert read_usage(odd, 0) == (read_usage(odd, 0)[0], odd.stat().st_size)
    assert read_usage(odd, 0)[0].replies == ()
    assert read_usage(odd, 0)[0].prompts == ()


def test_read_replies_stops_at_partial_line(tmp_path: Path) -> None:
    data = SESSION.read_bytes()
    path = tmp_path / "s.jsonl"
    path.write_bytes(data[:-10])
    first, offset = read_usage(path, 0)
    assert offset == data[:-1].rfind(b"\n") + 1  # the start of the last line
    assert [p.key for p in first.prompts] == ["p-1"]
    with path.open("ab") as f:
        f.write(data[-10:])
    rest, end = read_usage(path, offset)
    assert [p.key for p in rest.prompts] == ["p-2"]
    assert rest.replies == ()
    assert end == len(data)


def test_read_replies_keeps_sidechain() -> None:
    usage, _ = read_usage(FIXTURES / "sub" / "agent-x.jsonl", 0)
    assert [r.model for r in usage.replies] == ["claude-haiku-4-5-20251001"]
    assert usage.prompts == ()  # Claude prompting its subagent, not the user typing


def test_read_usage_prompts() -> None:
    usage, _ = read_usage(SESSION, 0)
    assert usage.prompts == (
        Prompt("p-1", _ts("2026-10-20T10:00:00+00:00"), SESSION_ID, "/home/user/webshop", 15),
        Prompt("p-2", _ts("2026-10-20T10:05:00+00:00"), SESSION_ID, "/home/user/webshop", 12),
    )


def test_read_usage_missing_file(tmp_path: Path) -> None:
    assert read_usage(tmp_path / "gone.jsonl", 7) == (read_usage(tmp_path / "x", 0)[0], 7)


def test_transcript_files_any_depth_and_since(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    shutil.copytree(FIXTURES, root / "-home-user-webshop")
    old = root / "-home-user-webshop" / "resumed.jsonl"
    os.utime(old, (1_000_000.0, 1_000_000.0))
    found = transcript_files(root, since=2_000_000.0)
    names = sorted(f.path.relative_to(root).as_posix() for f in found)
    assert names == ["-home-user-webshop/session.jsonl", "-home-user-webshop/sub/agent-x.jsonl"]
    session = next(f for f in found if f.path.name == "session.jsonl")
    st = session.path.stat()
    assert (session.size, session.inode, session.mtime) == (st.st_size, st.st_ino, st.st_mtime)
    assert transcript_files(tmp_path / "nowhere", since=0.0) == []


def test_read_usage_reads_at_most_limit(tmp_path: Path) -> None:
    data = SESSION.read_bytes()
    offset, keys = 0, []
    while offset < len(data):
        usage, end = read_usage(SESSION, offset, limit=1500)
        assert end > offset  # always progresses
        longest = max(len(line) + 1 for line in data.splitlines())
        assert end - offset <= 1500 + longest  # a chunk, read on to the end of a line
        keys += [r.key for r in usage.replies]
        offset = end
    # a reply's lines can fall in two chunks: the store keeps it once
    assert list(dict.fromkeys(keys)) == ["msg_u1:req_u1", "msg_u2:req_u2"]


def test_read_usage_line_longer_than_limit(tmp_path: Path) -> None:
    path = tmp_path / "long.jsonl"
    first = SESSION.read_text().splitlines()[1]  # the first prompt, ~400 bytes
    path.write_text(first + "\n" + first.replace("p-1", "p-9") + "\n")
    usage, end = read_usage(path, 0, limit=50)
    assert [p.key for p in usage.prompts] == ["p-1"]
    assert end == len(first) + 1
