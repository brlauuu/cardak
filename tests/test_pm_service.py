import json
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from cardak.collectors.github import GithubError, Issue, Merged, RepoData
from cardak.core.config import Config, Project, SummarySettings
from cardak.core.pm import NOT_ON_GITHUB
from cardak.core.pm_service import (
    EVENTS_EVERY,
    LOCAL_EVERY,
    MIN_GAP,
    REFRESH_EVERY,
    TRIGGER_DELAY,
    PmService,
    changes_github,
)
from cardak.core.store import Store

T0 = 2_000_000_000.0
U = "https://github.com/example/webshop"
WEBSHOP = Path("/w/webshop")
API = Path("/w/api-gw")
NOTES = Path("/w/notes")


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> float:
        return self.now


class Git:
    """Answers the service's git reads from dicts, per folder."""

    def __init__(self) -> None:
        self.branches: dict[Path, list[str]] = {WEBSHOP: ["main"], API: ["main"], NOTES: ["main"]}
        self.remotes = {
            WEBSHOP: "git@github.com:example/webshop.git",
            API: "https://github.com/example/api-gw",
        }
        self.checked_out: dict[Path, str] = {}

    def __call__(self, argv: Sequence[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        where = Path(argv[argv.index("-C") + 1])
        if "for-each-ref" in argv:
            out: str | None = "\n".join(self.branches.get(where, []))
        elif "remote" in argv:
            out = self.remotes.get(where)
        else:
            out = self.checked_out.get(where)
        if out is None:
            return subprocess.CompletedProcess(argv, 1, "", "fatal")
        return subprocess.CompletedProcess(argv, 0, out + "\n", "")


def data(*numbers: int, merged: tuple[Merged, ...] = ()) -> RepoData:
    issues = tuple(Issue(n, f"issue {n}", (), T0 - n, f"{U}/issues/{n}") for n in numbers)
    return RepoData(issues, (), merged, ())


class Fetch:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.replies: dict[str, RepoData | GithubError] = {}

    def __call__(self, repo: str, since: float) -> RepoData:
        self.calls.append(repo)
        reply = self.replies.get(repo, data(1, 2))
        if isinstance(reply, GithubError):
            raise reply
        return reply


@pytest.fixture
def parts(tmp_path: Path):  # noqa: ANN201
    clock, git, fetch = Clock(), Git(), Fetch()
    config = Config(
        projects=(Project("webshop", WEBSHOP), Project("api-gw", API), Project("notes", NOTES)),
        summary=SummarySettings(0),  # off: done is the plain list, no provider
    )
    store = Store(tmp_path / "db")
    svc = PmService(
        lambda: config,
        store,
        keys_dir=tmp_path / "keys",
        tools=None,
        fetch=fetch,
        git_runner=git,
        board_file=tmp_path / "state" / "pm.json",
        cache=tmp_path / "cache",
        clock=clock,
    )
    yield svc, clock, git, fetch, store, tmp_path
    store.close()


def band(svc: PmService, name: str):  # noqa: ANN201
    board = svc.board()
    assert board is not None
    return next(b for b in board.bands if b.name == name)


def event(store: Store, ts: float, command: str, cwd: str = "/w/webshop/src") -> None:
    payload = {
        "session_id": "s",
        "hook_event_name": "PostToolUse",
        "cwd": cwd,
        "tool_name": "Bash",
        "tool_input": {"command": command},
    }
    store.record_event("s", "PostToolUse", json.dumps(payload), ts)


def test_first_step_fetches_each_repo_once(parts) -> None:  # noqa: ANN001
    svc, clock, git, fetch, *_ = parts
    git.remotes[NOTES] = "git@github.com:example/webshop.git"  # shares webshop's repo
    svc.step()
    assert sorted(fetch.calls) == ["example/api-gw", "example/webshop"]
    assert [b.name for b in svc.board().bands] == ["webshop", "api-gw", "notes"]  # type: ignore[union-attr]
    assert band(svc, "notes").repo == "example/webshop" and band(svc, "notes").todo


def test_project_github_setting_wins(parts, tmp_path: Path) -> None:  # noqa: ANN001
    svc, clock, git, fetch, store, _ = parts
    config = Config(projects=(Project("webshop", WEBSHOP, github="example/other"),))
    svc._config = lambda: config  # noqa: SLF001
    svc.step()
    assert fetch.calls == ["example/other"]


def test_not_on_github(parts) -> None:  # noqa: ANN001
    svc, *_ = parts
    svc.step()
    assert band(svc, "notes").error == NOT_ON_GITHUB


def test_fresh_cache_is_not_refetched_at_start(parts) -> None:  # noqa: ANN001
    svc, clock, git, fetch, store, tmp = parts
    svc.step()
    clock.now += 600
    again = PmService(
        svc._config,
        store,
        keys_dir=tmp / "keys",
        tools=None,
        fetch=fetch,
        git_runner=git,  # noqa: SLF001
        board_file=tmp / "state" / "pm.json",
        cache=tmp / "cache",
        clock=clock,
    )
    again.step()
    assert len(fetch.calls) == 2  # only the first service's
    assert [c.number for c in band(again, "webshop").todo] == [1, 2]
    assert band(again, "webshop").fetched_at == T0


def test_hourly(parts) -> None:  # noqa: ANN001
    svc, clock, git, fetch, *_ = parts
    svc.step()
    clock.now = T0 + REFRESH_EVERY - 1
    svc.step()
    assert len(fetch.calls) == 2
    clock.now = T0 + REFRESH_EVERY + 1
    svc.step()
    assert len(fetch.calls) == 4


def test_refresh_one_project_respects_min_gap(parts) -> None:  # noqa: ANN001
    svc, clock, git, fetch, *_ = parts
    svc.step()
    svc.refresh(WEBSHOP)
    clock.now += 1
    svc.step()
    assert len(fetch.calls) == 2
    clock.now = T0 + MIN_GAP + 1
    svc.step()
    assert fetch.calls[2:] == ["example/webshop"]


def test_refresh_all(parts) -> None:  # noqa: ANN001
    svc, clock, git, fetch, *_ = parts
    svc.step()
    clock.now = T0 + MIN_GAP + 1
    svc.refresh()
    svc.step()
    assert sorted(fetch.calls[2:]) == ["example/api-gw", "example/webshop"]


def test_github_event_triggers_after_delay(parts) -> None:  # noqa: ANN001
    svc, clock, git, fetch, store, _ = parts
    svc.step()
    clock.now += 70
    event(store, clock.now, "gh pr merge 3")
    clock.now += EVENTS_EVERY
    svc.step()
    assert len(fetch.calls) == 2  # not before TRIGGER_DELAY
    clock.now = T0 + 70 + TRIGGER_DELAY + 1
    svc.step()
    assert fetch.calls[2:] == ["example/webshop"]


def test_event_older_than_the_cache_is_ignored(parts) -> None:  # noqa: ANN001
    svc, clock, git, fetch, store, _ = parts
    event(store, T0 - 10, "git push")
    svc.step()
    clock.now += TRIGGER_DELAY + EVENTS_EVERY + 1
    svc.step()
    assert len(fetch.calls) == 2


@pytest.mark.parametrize(
    "command, hit",
    [
        ("gh pr create -t x", True),
        ("git push -u origin x", True),
        ("cd a && gh issue comment 3 -b y", True),
        ("gh release create v1", True),
        ("gh run list", False),
        ("echo git pushes", False),
        ("ls", False),
    ],
)
def test_changes_github(command: str, hit: bool) -> None:
    payload = {
        "session_id": "s",
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
    }
    assert changes_github(json.dumps(payload)) is hit


def test_changes_github_mcp_and_garbage() -> None:
    mcp = {"session_id": "s", "hook_event_name": "PostToolUse", "tool_name": "mcp__github__merge"}
    assert changes_github(json.dumps(mcp)) and not changes_github("not json")


def test_error_keeps_last_good_data(parts) -> None:  # noqa: ANN001
    svc, clock, git, fetch, *_ = parts
    svc.step()
    fetch.replies["example/webshop"] = GithubError("can't reach GitHub")
    clock.now = T0 + REFRESH_EVERY + 1
    svc.step()
    b = band(svc, "webshop")
    assert b.error == "can't reach GitHub" and b.todo and b.fetched_at == T0


def test_done_written_once_per_input(parts, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    from cardak.core import pm_service
    from cardak.core.pm import Done as DoneView

    calls: list[str] = []

    def write_done(repo: str, data: RepoData, now: float, **kw: Any) -> tuple[DoneView, bool]:
        calls.append(repo)
        return DoneView((), "x"), True

    monkeypatch.setattr(pm_service, "write_done", write_done)
    svc, clock, git, fetch, *_ = parts
    svc.step()
    clock.now = T0 + REFRESH_EVERY + 1
    svc.step()
    assert sorted(calls) == ["example/api-gw", "example/webshop"]


def test_unsettled_done_retried_next_fetch(parts, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    from cardak.core import pm_service
    from cardak.core.pm import Done as DoneView

    calls: list[str] = []

    def write_done(repo: str, data: RepoData, now: float, **kw: Any) -> tuple[DoneView, bool]:
        calls.append(repo)
        return DoneView((), "failed"), False

    monkeypatch.setattr(pm_service, "write_done", write_done)
    svc, clock, git, fetch, *_ = parts
    svc.step()
    clock.now = T0 + REFRESH_EVERY + 1
    svc.step()
    assert len(calls) == 4


def test_board_file_written_only_on_change(parts) -> None:  # noqa: ANN001
    svc, clock, git, fetch, store, tmp = parts
    svc.step()
    path = tmp / "state" / "pm.json"
    first = path.read_text()
    assert json.loads(first)["bands"][0]["name"] == "webshop"
    path.write_text("touched")  # a rewrite would replace this
    clock.now += LOCAL_EVERY + 1
    svc.step()
    assert path.read_text() == "touched"
    assert not list(path.parent.glob(".pm.json.*"))  # no temp files left behind


def test_local_branch_moves_card_without_fetch(parts) -> None:  # noqa: ANN001
    svc, clock, git, fetch, *_ = parts
    svc.step()
    git.branches[WEBSHOP] = ["main", "2-fix"]
    clock.now += LOCAL_EVERY + 1
    svc.step()
    assert [c.number for c in band(svc, "webshop").onit] == [2]
    assert len(fetch.calls) == 2


def test_session_state_on_onit_card(parts) -> None:  # noqa: ANN001
    svc, clock, git, fetch, *_ = parts
    git.branches[WEBSHOP] = ["main", "2-fix"]
    git.checked_out[Path("/w/webshop-wt")] = "2-fix"
    svc.observe([(WEBSHOP, Path("/w/webshop-wt"), "working"), (WEBSHOP, None, "waiting")])
    svc.step()
    assert band(svc, "webshop").onit[0].session == "working"


def test_step_survives_a_store_error(parts) -> None:  # noqa: ANN001
    svc, clock, git, fetch, store, _ = parts
    store.close()
    svc.step()  # events can't be read; the board still builds
    assert band(svc, "webshop").todo


def test_board_file_rereads_on_change(tmp_path: Path) -> None:
    import os

    from cardak.core.pm import Board, board_to_json
    from cardak.core.pm_service import BoardFile

    path = tmp_path / "pm.json"
    reader = BoardFile(path)
    assert reader.get() is None
    path.write_text(json.dumps(board_to_json(Board((), 1.0))))
    assert reader.get() == Board((), 1.0)
    path.write_text(json.dumps(board_to_json(Board((), 22.0))))
    os.utime(path, ns=(1, 1))  # a different mtime, whatever the clock's resolution
    assert reader.get() == Board((), 22.0)
    path.write_text("garbage")
    os.utime(path, ns=(2, 2))
    assert reader.get() is None


def test_board_shows_each_repo_as_soon_as_it_is_read(parts) -> None:  # noqa: ANN001
    svc, clock, git, fetch, *_ = parts
    seen: list[object] = []

    def watching(repo: str, since: float) -> RepoData:
        board = svc.board()
        seen.append(None if board is None else [b.name for b in board.bands if b.todo])
        return data(1, 2)

    svc._fetch = watching  # noqa: SLF001
    svc.step()
    assert seen[0] == []  # a board, without data yet, before the first fetch
    assert seen[1] == ["webshop"]  # the first repo shows while the second is read


def test_cards_show_before_the_done_summary(parts, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    from cardak.core import pm_service
    from cardak.core.pm import Done as DoneView

    svc, *_ = parts
    during: list[bool] = []

    def write_done(repo: str, data: RepoData, now: float, **kw: Any) -> tuple[DoneView, bool]:
        board = svc.board()
        during.append(board is not None and any(b.repo == repo and b.todo for b in board.bands))
        return DoneView((), "x"), True

    monkeypatch.setattr(pm_service, "write_done", write_done)
    svc.step()
    assert during and all(during)
