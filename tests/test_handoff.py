from datetime import datetime
from pathlib import Path

import pytest

from cardak.collectors.handoff import NOTE, parse_entries, read_note

FIX = Path(__file__).parent / "fixtures" / "handoff" / "handoff.md"


def entries() -> list:
    return parse_entries(FIX.read_text())


def test_entries_in_file_order() -> None:
    es = entries()
    assert [e.when for e in es] == [
        datetime(2026, 10, 20, 9, 5),
        datetime(2026, 10, 22, 17, 40),
        datetime(2026, 10, 24, 14, 10),
    ]
    assert [e.branch for e in es] == ["main", None, "64-checkpoint"]
    assert es[0].body.startswith("**Goal:** Add a price filter")


def test_subheading_stays_in_body() -> None:
    es = entries()
    assert len(es) == 3
    assert "## Notes" in es[-1].body
    assert es[-1].body.endswith("**Next step:** Recap screen.")


def test_crlf_bom_no_trailing_newline() -> None:
    text = "﻿" + FIX.read_text().rstrip("\n").replace("\n", "\r\n")
    assert parse_entries(text) == entries()


def test_preamble_and_junk_are_not_entries() -> None:
    assert parse_entries("# Handoff notes\n\nsome text\n") == []


def test_bad_date_heading_is_body() -> None:
    text = "## 2026-10-20 09:05\none\n## 2026-13-40 99:99 · x\ntwo\n"
    [e] = parse_entries(text)
    assert "## 2026-13-40 99:99 · x" in e.body


def test_read_note_absent_and_unreadable(tmp_path: Path) -> None:
    assert read_note(tmp_path) is None
    (tmp_path / NOTE).mkdir(parents=True)  # a directory where the file should be
    assert read_note(tmp_path) is None
    (tmp_path / NOTE).rmdir()
    (tmp_path / NOTE).write_text("")
    assert read_note(tmp_path) == []
    (tmp_path / NOTE).write_bytes(b"\xff\xfe## bad")
    assert read_note(tmp_path) is None


def test_read_note_entries(tmp_path: Path) -> None:
    (tmp_path / NOTE).parent.mkdir()
    (tmp_path / NOTE).write_text(FIX.read_text())
    assert read_note(tmp_path) == entries()


def test_unclosed_fence_does_not_hide_later_entries() -> None:
    text = (
        "## 2026-10-20 09:05 · main\n\n```python\nprint('never closed')\n\n"
        "## 2026-10-22 17:40 · main\n\nlater\n"
    )
    first, second = parse_entries(text)
    assert "```python" in first.body and "never closed" in first.body
    assert second.when == datetime(2026, 10, 22, 17, 40) and second.body == "later"


def test_paired_fences_before_an_unclosed_one_still_hide_headings() -> None:
    text = (
        "## 2026-10-20 09:05\n\n```\n## 2026-01-01 00:00 · fenced\n```\n\n"
        "```\n\n## 2026-10-22 17:40\n\nlater\n"
    )
    first, second = parse_entries(text)
    assert "## 2026-01-01 00:00 · fenced" in first.body
    assert second.body == "later"


@pytest.mark.parametrize("dash", ["-", "–", "—"])
def test_dash_before_branch(dash: str) -> None:
    [e] = parse_entries(f"## 2026-10-25 14:00 {dash} main\n\nbody\n")
    assert e.when == datetime(2026, 10, 25, 14, 0) and e.branch == "main"


@pytest.mark.parametrize(
    "line", ["## Notes - main", "## 2026-10-25 - main", "## 2026-10-25 14:00-main", "## a · b"]
)
def test_undated_or_malformed_headings_stay_body(line: str) -> None:
    [e] = parse_entries(f"## 2026-10-20 09:05\n\n{line}\n")
    assert line in e.body
