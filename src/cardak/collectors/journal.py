"""The only reader of the systemd journal: `journalctl -o json`, OOM kills and failed scopes.

Kernel messages need membership in `adm` or `systemd-journal`; without it journalctl prints a
hint and nothing else, which reads here as "unreadable" (None), not as "no OOM kill".
"""

import json
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass

Runner = Callable[..., subprocess.CompletedProcess[str]]
_UNREADABLE = "not seeing messages from other users"
_KILLED = re.compile(r"Killed process (\d+) \(.*?anon-rss:(\d+)kB")
_FAILED = re.compile(r"^(\S+\.scope): Failed with result 'oom-kill'")


@dataclass(frozen=True)
class JournalEntry:
    ts: float
    message: str


@dataclass(frozen=True)
class OomKill:
    ts: float
    pid: int
    task: str
    memcg: str  # the killed task's cgroup path, e.g. …/app-org.wezfurlong.wezterm-<id>.scope
    anon_rss: int | None  # bytes, from the "Killed process" line when present


def parse_entries(text: str) -> list[JournalEntry]:
    out: list[JournalEntry] = []
    for line in text.splitlines():
        try:
            data = json.loads(line)
            message = data["MESSAGE"]
            ts = int(data["__REALTIME_TIMESTAMP"]) / 1e6
        except (ValueError, KeyError, TypeError):
            continue
        if isinstance(message, str):  # non-UTF-8 messages come as a list of byte values
            out.append(JournalEntry(ts, message))
    return out


def oom_kills(entries: Sequence[JournalEntry]) -> list[OomKill]:
    rss: dict[int, int] = {}
    for e in entries:
        if m := _KILLED.search(e.message):
            rss[int(m.group(1))] = int(m.group(2)) * 1024
    kills: list[OomKill] = []
    for e in entries:
        if not e.message.startswith("oom-kill:"):
            continue
        fields = dict(token.split("=", 1) for token in e.message[9:].split(",") if "=" in token)
        try:
            pid = int(fields["pid"])
            kills.append(OomKill(e.ts, pid, fields["task"], fields["task_memcg"], rss.get(pid)))
        except (KeyError, ValueError):
            continue
    return kills


def failed_scopes(entries: Sequence[JournalEntry]) -> set[str]:
    return {m.group(1) for e in entries if (m := _FAILED.match(e.message))}


def _run(argv: list[str], runner: Runner) -> subprocess.CompletedProcess[str] | None:
    try:
        return runner(argv, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None


def read_journal(
    since: float, until: float, runner: Runner = subprocess.run
) -> tuple[list[JournalEntry] | None, list[JournalEntry]]:
    """(kernel, user manager) entries in [since, until]; kernel is None when unreadable."""
    window = ["-o", "json", "--no-pager", "--since", f"@{since:.0f}", "--until", f"@{until:.0f}"]
    out = _run(["journalctl", *window, "_TRANSPORT=kernel"], runner)
    kernel = None
    if out is not None and out.returncode == 0 and _UNREADABLE not in out.stderr:
        kernel = parse_entries(out.stdout)
    if out is None:
        return None, []
    user_out = _run(["journalctl", "--user", *window], runner)
    if user_out is None or user_out.returncode != 0:
        return kernel, []
    return kernel, parse_entries(user_out.stdout)
