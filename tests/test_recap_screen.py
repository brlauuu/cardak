import os
import subprocess
import threading
from datetime import datetime
from pathlib import Path

import pytest
from textual.app import App
from textual.widgets import Markdown, OptionList, Static

from cardak.collectors.handoff import NOTE
from cardak.core import recap
from cardak.ui.recap import RecapScreen

NOW = datetime(2026, 10, 25, 14, 10).timestamp()


@pytest.fixture(autouse=True)
def git_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "test@example.com")


def git(repo: Path, *args: str, ts: float | None = None) -> None:
    env = dict(os.environ)
    if ts is not None:
        env |= {"GIT_AUTHOR_DATE": f"@{int(ts)}", "GIT_COMMITTER_DATE": f"@{int(ts)}"}
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


def write_note(top: Path, text: str) -> None:
    (top / NOTE).parent.mkdir(exist_ok=True)
    (top / NOTE).parent.joinpath(".gitignore").write_text("*\n")  # as /checkpoint writes it
    (top / NOTE).write_text(text)


class Host(App[None]):
    def __init__(self, screen: RecapScreen) -> None:
        super().__init__()
        self._recap = screen

    def on_mount(self) -> None:
        self.push_screen(self._recap)


async def texts(screen: RecapScreen, check) -> None:  # noqa: ANN001
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        await screen.workers.wait_for_complete()
        await pilot.pause()
        text = "\n".join(str(s.render()) for s in screen.query(Static) if s.id in ("head", "since"))
        check(text)


async def test_recap_screen_markup_safe(tmp_path: Path) -> None:
    repo = tmp_path / "webshop"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    write_note(repo, "## 2026-10-24 14:10 · main\n\n**Next step:** handle [/x] and [b]\n")
    (repo / "a").write_text("a")
    git(repo, "add", "a")
    git(repo, "commit", "-q", "-m", "[b]x subject", ts=datetime(2026, 10, 24, 15, 0).timestamp())
    screen = RecapScreen("web[/x]shop", [repo], NOW)

    def check(text: str) -> None:
        assert "· main · checkpoint 1 day ago" in text
        assert "Since then: 1 commit" in text and "[b]x subject" in text
        assert "0 changed, 0 untracked" in text
        assert "[/x]" in screen.query_one("#note", Markdown).source

    await texts(screen, check)


async def test_recap_no_note_message(tmp_path: Path) -> None:
    screen = RecapScreen("webshop", [tmp_path], NOW)

    def check(text: str) -> None:
        assert "No checkpoint yet. Press c on an agent pane to write one." in text
        assert "Not a git repository." in text
        assert not screen.query_one("#others", OptionList).display

    await texts(screen, check)


async def test_recap_others_switch(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    write_note(a, "## 2026-10-20 09:05\n\nolder note\n")
    write_note(b, "## 2026-10-24 14:10 · w-[/x]\n\nnewer note\n")
    screen = RecapScreen("webshop", [a, b], NOW)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        await screen.workers.wait_for_complete()
        await pilot.pause()
        assert "newer note" in screen.query_one("#note", Markdown).source
        assert "· w-[/x] · checkpoint" in str(screen.query_one("#head", Static).render())
        others = screen.query_one("#others", OptionList)
        assert others.display and others.option_count == 1
        others.focus()
        others.highlighted = 0
        await pilot.press("enter")
        await screen.workers.wait_for_complete()
        await pilot.pause()
        assert "older note" in screen.query_one("#note", Markdown).source
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, RecapScreen)


async def test_recap_says_more_when_the_commit_list_is_cut(tmp_path: Path) -> None:
    repo = tmp_path / "webshop"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    write_note(repo, "## 2026-10-24 14:10 · main\n\nnote\n")
    after = datetime(2026, 10, 24, 15, 0).timestamp()
    for i in range(recap.COMMITS + 1):
        git(repo, "commit", "-q", "--allow-empty", "-m", f"c{i}", ts=after + i)
    screen = RecapScreen("webshop", [repo], NOW)

    def check(text: str) -> None:
        assert f"Since then: more than {recap.COMMITS} commits, the latest {recap.COMMITS}:" in text
        assert text.endswith(" c1\n0 changed, 0 untracked")  # c0, the oldest, is cut

    await texts(screen, check)


async def test_recap_late_result_of_an_earlier_pick_is_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, b, c = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    for d, day in ((a, 20), (b, 22), (c, 24)):
        d.mkdir()
        write_note(d, f"## 2026-10-{day} 09:05\n\nnote {d.name}\n")
    release = threading.Event()
    real = recap.since

    def since(note):  # noqa: ANN001, ANN202
        if note.top == a:
            release.wait(5)  # a slow git: its answer arrives after b's
        return real(note)

    monkeypatch.setattr(recap, "since", since)
    screen = RecapScreen("webshop", [a, b, c], NOW)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        await screen.workers.wait_for_complete()
        await pilot.pause()
        assert "note c" in screen.query_one("#note", Markdown).source
        others = screen.query_one("#others", OptionList)
        others.focus()
        others.highlighted = 1  # the others are b, then a: newest first
        await pilot.press("enter")  # a: blocks
        await pilot.press("up", "enter")  # b: answers first
        for _ in range(50):
            await pilot.pause(0.02)
            if "note b" in screen.query_one("#note", Markdown).source:
                break
        assert "note b" in screen.query_one("#note", Markdown).source
        release.set()
        await screen.workers.wait_for_complete()
        await pilot.pause()
        assert "note b" in screen.query_one("#note", Markdown).source
        assert "/b" in str(screen.query_one("#head", Static).render())
