from datetime import datetime
from pathlib import Path

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
