import json

import pytest

from cardak.collectors.github import Issue, Label, PullRequest, RepoData
from cardak.core.pm import (
    NOT_ON_GITHUB,
    NOTHING_DONE,
    Band,
    Board,
    Bullet,
    Card,
    Done,
    board_from_json,
    board_to_json,
    build_band,
    issue_number,
)

U = "https://github.com/example/webshop"
BUG = Label("bug", "d73a4a")
NO_DONE = Done((), NOTHING_DONE)


def issue(n: int, updated: float = 0.0, labels: tuple[Label, ...] = ()) -> Issue:
    return Issue(n, f"issue {n}", labels, updated, f"{U}/issues/{n}")


def pr(n: int, branch: str, closes: tuple[int, ...] = (), updated: float = 0.0) -> PullRequest:
    return PullRequest(n, f"pr {n}", branch, (), updated, f"{U}/pull/{n}", closes)


def band(data: RepoData, local: list[str] | None = None, sessions=None) -> Band:  # noqa: ANN001
    return build_band(
        "webshop",
        "/w/webshop",
        "example/webshop",
        data,
        local or [],
        sessions or {},
        NO_DONE,
        1.0,
        None,
    )


@pytest.mark.parametrize(
    "branch, number",
    [
        ("68-pm-view", 68),
        ("feat/68-pm-view", 68),
        ("68", 68),
        ("68_x", 68),
        ("release-0.15.0", None),
        ("v2-redesign", None),
        ("fix/2024cleanup", None),
        ("0-zero", None),
        ("main", None),
    ],
)
def test_issue_number(branch: str, number: int | None) -> None:
    assert issue_number(branch) == number


def test_onit_from_local_branch_pr_closing_and_pr_branch() -> None:
    data = RepoData(
        (issue(1), issue(2), issue(3), issue(4)),
        (pr(10, "x", closes=(2,)), pr(11, "feat/3-b")),
        (),
        (),
    )
    b = band(data, local=["1-a", "main"])
    onit = {c.number: c for c in b.onit}
    assert sorted(onit) == [1, 2, 3] and [c.number for c in b.todo] == [4]
    assert (onit[2].pr, onit[2].branch) == (10, "x")
    assert (onit[1].branch, onit[1].pr) == ("1-a", None)
    assert (onit[3].branch, onit[3].pr) == ("feat/3-b", 11)
    assert all(c.kind == "issue" for c in b.onit)


def test_local_branch_wins_over_the_prs() -> None:
    b = band(RepoData((issue(5),), (pr(12, "pr-side", closes=(5,)),), (), ()), local=["5-mine"])
    assert (b.onit[0].branch, b.onit[0].pr) == ("5-mine", 12)


def test_pr_without_issue_is_a_pr_card() -> None:
    prs = (pr(12, "fix-typo", updated=2.0), pr(13, "99-closed-issue", updated=1.0))
    b = band(RepoData((issue(1),), prs, (), ()))
    assert [(c.kind, c.number, c.branch) for c in b.onit] == [
        ("pr", 12, "fix-typo"),
        ("pr", 13, "99-closed-issue"),  # names an issue that isn't open: still in progress
    ]
    assert b.onit[0].url == f"{U}/pull/12" and b.onit[0].pr == 12


def test_sorted_newest_update_first() -> None:
    data = RepoData((issue(5, 50.0), issue(6, 10.0), issue(7, 90.0)), (), (), ())
    assert [c.number for c in band(data).todo] == [7, 5, 6]
    mixed = RepoData((issue(1, 5.0),), (pr(12, "fix-typo", updated=9.0),), (), ())
    assert [c.number for c in band(mixed, local=["1-a"]).onit] == [12, 1]


def test_card_keeps_labels() -> None:
    assert band(RepoData((issue(1, labels=(BUG,)),), (), (), ())).todo[0].labels == (BUG,)


def test_session_state_on_its_branch() -> None:
    b = band(RepoData((issue(1),), (), (), ()), local=["1-a"], sessions={"1-a": "waiting"})
    assert b.onit[0].session == "waiting"


def test_empty_repo_renders() -> None:
    b = band(RepoData((), (), (), ()))
    assert b.todo == b.onit == () and b.done.note == NOTHING_DONE and b.error is None


def test_no_data_yet() -> None:
    b = build_band("x", "/p", "example/x", None, ["1-a"], {}, NO_DONE, None, "can't reach GitHub")
    assert b.todo == b.onit == () and b.error == "can't reach GitHub"


def test_not_on_github() -> None:
    b = build_band("x", "/p", None, None, [], {}, Done((), None), None, None)
    assert b.error == NOT_ON_GITHUB and b.repo is None


BOARD = Board(
    (
        Band(
            "webshop",
            "/w/webshop",
            "example/webshop",
            (Card("issue", 212, "Checkout", (BUG,), 5.0, f"{U}/issues/212"),),
            (Card("pr", 210, "Typo", (), 6.0, f"{U}/pull/210", "fix-typo", 210, "working"),),
            Done((Bullet("Fixed tax rounding", (203,), f"{U}/pull/203"),), None),
            4.0,
            None,
        ),
        Band("notes", "/w/notes", None, (), (), Done((), None), None, NOT_ON_GITHUB),
    ),
    7.0,
)


def test_board_json_round_trip_and_garbage() -> None:
    assert board_from_json(json.loads(json.dumps(board_to_json(BOARD)))) == BOARD
    assert board_from_json({"bands": "nope"}) is None
    assert board_from_json({"bands": [{"name": 3}], "built_at": 1}) is None
    assert board_from_json(None) is None
