import os
import subprocess
from datetime import datetime
from pathlib import Path

import pytest
from textual.app import App
from textual.widgets import Markdown, OptionList, Static

from cardak.collectors.handoff import NOTE
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
