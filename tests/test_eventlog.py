import time
from collections.abc import Iterator

import pytest

from cardak.core.eventlog import LogLine, line_text, log_lines
from cardak.core.store import ResourceEvent

WEB = "project:/home/user/repos/webshop"
T0 = 1792682880.0  # 2026-10-22 15:28 UTC


@pytest.fixture(autouse=True)
def utc(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def ev(
    ts: float,
    kind: str = "big",
    notified: bool = True,
    subject: str = WEB,
    text: str = "webshop · pytest in pane 12 · 8.5 GB",
    level: str = "warning",
) -> ResourceEvent:
    return ResourceEvent(ts, kind, level, subject, text, notified)


def test_repeats_fold_into_the_notified_line() -> None:
    events = [ev(T0), ev(T0 + 60, notified=False), ev(T0 + 120, "busy"),
              ev(T0 + 840, notified=False), ev(T0 + 900, notified=True)]  # fmt: skip
    lines = log_lines(events)
    assert [(x.ts, x.kind, x.repeats, x.until) for x in lines] == [
        (T0 + 900, "big", 0, None),
        (T0 + 120, "busy", 0, None),
        (T0, "big", 2, T0 + 840),
    ]


def test_unmatched_repeat_stands_alone() -> None:
    [line] = log_lines([ev(T0, notified=False)])  # its notified line is older than the window
    assert (line.repeats, line.until) == (0, None)


def test_crash_line_first_sentence_and_full() -> None:
    text = "WezTerm was closed at 15:28:48 on Oct 22. The machine ran out of memory (32 GB)."
    [crash, quiet] = log_lines([
        ev(T0, "crash", True, "wezterm:7", "WezTerm (pid 8) closed at 15:00:00 on Oct 22; x.",
           "info"),
        ev(T0 + 1, "crash", True, "wezterm:7", text, "critical"),
    ])  # fmt: skip
    assert crash.text == "WezTerm was closed at 15:28:48 on Oct 22." and crash.full == text
    assert quiet.full is None


def test_line_text_format() -> None:
    line = LogLine(T0, "warning", "big", WEB, "webshop · pytest", None, 4, T0 + 840)
    assert line_text(line) == "Oct 22 15:28  big  webshop · pytest  (repeated 4× until 15:42)"
    crash = LogLine(T0, "critical", "crash", "wezterm:7", "WezTerm was closed.", "x", 0, None)
    assert line_text(crash) == "Oct 22 15:28  WezTerm closed  WezTerm was closed."
