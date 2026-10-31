import json
import subprocess
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from cardak.collectors.github import TIMEOUT, GithubError, Label, fetch, repo_from_url

FIXTURES = Path(__file__).parent / "fixtures" / "github"
SINCE = datetime(2026, 10, 24, tzinfo=UTC).timestamp()


class Replies:
    """Answers each `gh api graphql` call with the next fixture, and keeps the calls."""

    def __init__(self, *names: str) -> None:
        self.names = list(names)
        self.calls: list[tuple[Sequence[str], dict[str, Any]]] = []

    def __call__(self, argv: Sequence[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append((argv, kw))
        out = (FIXTURES / f"{self.names.pop(0)}.json").read_text()
        return subprocess.CompletedProcess(argv, 0, out, "")


def test_repo_from_url() -> None:
    assert repo_from_url("git@github.com:example/webshop.git") == "example/webshop"
    assert repo_from_url("https://github.com/example/webshop") == "example/webshop"
    assert repo_from_url("https://github.com/example/webshop.git\n") == "example/webshop"
    assert repo_from_url("ssh://git@github.com/example/webshop.git") == "example/webshop"
    assert repo_from_url("https://gitlab.com/example/webshop.git") is None
    assert repo_from_url("https://github.com/example") is None


def test_fetch_paginates_and_filters() -> None:
    data = fetch("example/webshop", SINCE, run=Replies("page1", "issues_page2"))
    assert [i.number for i in data.issues] == [212, 198, 205, 190]
    assert data.issues[0].labels == (Label("bug", "d73a4a"),)
    assert data.issues[0].updated_at == datetime(2026, 10, 30, 8, tzinfo=UTC).timestamp()
    pr = data.prs[0]
    assert (pr.number, pr.branch, pr.closes) == (207, "190-cart-badge", (190,))
    assert [m.number for m in data.merged] == [203, 201]  # #150 merged before `since`
    assert data.merged[1].closes == (180,)
    assert [c.number for c in data.closed] == [180, 204]  # #151 not planned, #120 too old


def test_fetch_second_page_asks_for_issues_only() -> None:
    replies = Replies("page1", "issues_page2")
    fetch("example/webshop", SINCE, run=replies)
    argv, kw = replies.calls[0]
    assert list(argv[:3]) == ["gh", "api", "graphql"] and kw["timeout"] == TIMEOUT
    assert "owner=example" in argv and "name=webshop" in argv  # variables, never interpolated
    second = replies.calls[1][0]
    assert "after=C1" in second and "merged" not in " ".join(second)


def test_fetch_empty_repo() -> None:
    data = fetch("example/webshop", SINCE, run=Replies("empty"))
    assert data.issues == data.prs == data.merged == data.closed == ()


def _raising(error: BaseException):  # noqa: ANN202
    def run(argv: Sequence[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        raise error

    return run


def _failing(code: int, stdout: str, stderr: str):  # noqa: ANN202
    def run(argv: Sequence[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, code, stdout, stderr)

    return run


@pytest.mark.parametrize(
    "run, message",
    [
        (_raising(FileNotFoundError("gh")), "gh isn't installed"),
        (_raising(subprocess.TimeoutExpired("gh", TIMEOUT)), "can't reach GitHub"),
        (_failing(1, "", "error connecting to api.github.com\n"), "can't reach GitHub"),
        (
            _failing(1, "", "\nTo get started with GitHub CLI, run:  gh auth login\nmore"),
            "To get started with GitHub CLI, run:  gh auth login",
        ),
        (_failing(0, "not json", ""), "unexpected reply from GitHub"),
        (_failing(0, "{}", ""), "unexpected reply from GitHub"),
        (
            _failing(0, json.dumps({"data": {"repository": None}}), ""),
            "unexpected reply from GitHub",
        ),
    ],
)
def test_fetch_errors_are_one_line(run: Any, message: str) -> None:
    with pytest.raises(GithubError) as e:
        fetch("example/webshop", SINCE, run=run)
    assert str(e.value) == message


def test_graphql_errors_are_readable() -> None:
    body = json.dumps({"errors": [{"message": "Could not resolve to a Repository"}]})
    with pytest.raises(GithubError, match="^Could not resolve to a Repository$"):
        fetch("example/gone", SINCE, run=_failing(1, body, "gh: Could not resolve\n"))


def test_github_imports_nothing_from_cardak() -> None:
    source = (Path(__file__).parent.parent / "src/cardak/collectors/github.py").read_text()
    assert "from cardak" not in source and "import cardak" not in source
