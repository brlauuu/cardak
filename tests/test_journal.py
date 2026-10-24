import subprocess
from pathlib import Path

import pytest

from cardak.collectors.journal import (
    JournalEntry,
    failed_scopes,
    oom_kills,
    parse_entries,
    read_journal,
)

FIX = Path(__file__).parent / "fixtures" / "journal"
SCOPE = "app-org.wezfurlong.wezterm-0123456789abcdef0123456789abcdef.scope"


class FakeRun:
    def __init__(self, *results: tuple[int, str, str] | Exception) -> None:
        self.results = list(results)
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        code, out, err = result
        return subprocess.CompletedProcess(argv, code, out, err)


def entries(name: str) -> list[JournalEntry]:
    return parse_entries((FIX / name).read_text())


def test_parse_entries_skips_bad_lines() -> None:
    got = entries("user.jsonl")
    assert len(got) == 4
    assert got[0].ts == pytest.approx(1792589326.5)
    assert got[1].message == f"{SCOPE}: Failed with result 'oom-kill'."


def test_oom_kills_from_kernel_fixture() -> None:
    kills = oom_kills(entries("kernel.jsonl"))
    assert [(k.pid, k.task, k.anon_rss) for k in kills] == [
        (41872, "pytest", 14230296 * 1024),
        (40001, "python", None),
    ]
    assert kills[0].memcg.endswith("/" + SCOPE)
    assert kills[0].ts == 1792589326.370445


def test_failed_scopes() -> None:
    assert failed_scopes(entries("user.jsonl")) == {SCOPE}


def test_read_journal_calls() -> None:
    kernel = (FIX / "kernel.jsonl").read_text()
    user = (FIX / "user.jsonl").read_text()
    run = FakeRun((0, kernel, ""), (0, user, ""))
    got_kernel, got_user = read_journal(1000.0, 1060.0, run)
    assert got_kernel is not None and len(got_kernel) == 4 and len(got_user) == 4
    assert run.calls == [
        ["journalctl", "-o", "json", "--no-pager", "--since", "@1000", "--until", "@1060",
         "_TRANSPORT=kernel"],
        ["journalctl", "--user", "-o", "json", "--no-pager", "--since", "@1000", "--until",
         "@1060"],
    ]  # fmt: skip


def test_read_journal_unreadable() -> None:
    hint = "Hint: You are currently not seeing messages from other users and the system.\n"
    kernel, user = read_journal(0.0, 1.0, FakeRun((0, "", hint), (1, "", "boom")))
    assert kernel is None and user == []
    assert read_journal(0.0, 1.0, FakeRun(FileNotFoundError("journalctl"))) == (None, [])
    timeout = subprocess.TimeoutExpired("journalctl", 5)
    assert read_journal(0.0, 1.0, FakeRun((1, "", "no"), timeout)) == (None, [])
