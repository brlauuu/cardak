import json
from dataclasses import replace
from pathlib import Path

from textual.app import App
from textual.widgets import Collapsible
from textual.widgets._collapsible import CollapsibleTitle

from cardak.core.agents import SessionState
from cardak.core.summary import SummaryInfo
from cardak.ui.detail import DetailScreen, TranscriptScreen, detail_text

BASIC = Path(__file__).parent / "fixtures" / "transcripts" / "basic.jsonl"
NOW = 1000.0

PERMISSION = SessionState(
    "s1", pane_id=7, cwd=Path("/r/demo"), transcript_path=BASIC, status="waiting",
    kind="permission", status_since=NOW - 180, reason="Bash: uv run pytest",
    last_prompt="fix the symlink matching", last_prompt_at=NOW - 240, turn_edits=3,
    turn_reads=2, turn_commands=1, turn_files=("/r/demo/src/a.py", "/elsewhere/b.py"),
)  # fmt: skip


def plain(state: SessionState) -> str:
    return detail_text(state, "demo", NOW).plain


def test_detail_text_permission() -> None:
    text = plain(PERMISSION)
    assert text.splitlines()[0] == "● demo · claude  waiting 3m"
    for part in (
        "NEEDS  Approve a tool",
        "  Bash: uv run pytest",
        "YOU ASKED  (4m ago)",
        '  "fix the symlink matching"',
        "THIS TURN  3 edits, 2 reads, 1 command",
        "  edited src/a.py",
        "  edited /elsewhere/b.py",
    ):
        assert part in text.splitlines(), part


def test_detail_text_idle_and_working() -> None:
    idle = replace(PERMISSION, status="idle", kind=None, reason=None)
    assert "NEEDS  Nothing" in plain(idle).splitlines()
    working = replace(idle, status="working", current_tool="Edit x")
    assert "NEEDS  Working: Edit x" in plain(working).splitlines()
    fresh = SessionState("s2", status="idle", status_since=NOW)
    lines = plain(fresh).splitlines()
    assert "THIS TURN  nothing yet" in lines and not any(x.startswith("YOU ASKED") for x in lines)


def test_detail_trims_to_100_words() -> None:
    words = " ".join(f"w{i}" for i in range(300))
    finished = replace(PERMISSION, kind="finished", reason=words, last_message=words)
    body = plain(finished).splitlines()[3].strip()
    assert body.startswith("…") and body.endswith("w299")
    assert len(body.split()) <= 101  # "…w200" counts as one


class Host(App[None]):
    def __init__(self, screen: DetailScreen) -> None:
        super().__init__()
        self.detail = screen
        self.results: list[int | None] = []

    def on_mount(self) -> None:
        self.push_screen(self.detail, self.results.append)


async def test_detail_screen_keys() -> None:
    app = Host(DetailScreen(PERMISSION, "demo"))
    async with app.run_test() as pilot:
        await pilot.press("t")
        await pilot.pause()
        assert isinstance(app.screen, TranscriptScreen)
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "The tests pass" in str(app.screen.query_one("#entries").children[-1].render())
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert app.results == [7]

    app = Host(DetailScreen(PERMISSION, "demo"))
    async with app.run_test() as pilot:
        await pilot.press("escape")
        await pilot.pause()
        assert app.results == [None]


async def test_transcript_with_brackets_in_a_tool_call(tmp_path: Path) -> None:
    # review: a command containing "[/]" crashed the deck (Collapsible titles are markup)
    f = tmp_path / "t.jsonl"
    use = {
        "type": "tool_use",
        "id": "t1",
        "name": "Bash",
        "input": {"command": 'grep "[/]" f [id]'},
    }
    f.write_text(json.dumps({"type": "assistant", "message": {"content": [use]}}) + "\n")
    app = Host(DetailScreen(replace(PERMISSION, transcript_path=f), "demo"))
    async with app.run_test() as pilot:
        await pilot.press("t")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert isinstance(app.screen, TranscriptScreen)
        shown = [str(c.query_one(CollapsibleTitle).render()) for c in app.screen.query(Collapsible)]
        assert len(shown) == 1 and shown[0].endswith('Bash: grep "[/]" f [id]')


def test_detail_shows_summary_and_spend() -> None:
    text = detail_text(PERMISSION, "demo", NOW, SummaryInfo("Did x; needs y", 0.42, 5.0)).plain
    lines = text.splitlines()
    summary = lines.index("  Summary: Did x; needs y")
    assert "uv run pytest" in lines[summary + 1]  # the raw reason right after it
    assert lines[-1] == "Summaries this month: $0.42 of $5.00"


def test_detail_spend_without_a_summary() -> None:
    text = detail_text(PERMISSION, "demo", NOW, SummaryInfo(None, 0.0, 5.0)).plain
    assert "Summary:" not in text and text.endswith("Summaries this month: $0.00 of $5.00")


def test_detail_without_info_is_unchanged() -> None:
    assert "Summar" not in plain(PERMISSION)
