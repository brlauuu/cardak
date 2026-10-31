"""The PM view's board (#68): per project, open issues to do, work in progress, and what got done.

Pure: GitHub's data, the project's local branches and the Claude sessions on them go in, a
`Band` comes out. `pm_service` decides when to read them, `pm_done` writes the done column.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any, Literal

from cardak.collectors.github import Issue, Label, PullRequest, RepoData
from cardak.core.agents import Status

NOTHING_DONE = "Nothing done this week."
NOT_ON_GITHUB = "not on GitHub"
_NUMBER = re.compile(r"(\d+)(?:[-_]|$)")
_URGENCY: dict[Status, int] = {"waiting": 2, "working": 1, "idle": 0}

CardKind = Literal["issue", "pr"]


@dataclass(frozen=True)
class Card:
    kind: CardKind
    number: int
    title: str
    labels: tuple[Label, ...]
    updated_at: float
    url: str
    branch: str | None = None
    pr: int | None = None  # the open PR that closes or names it; a PR card's own number
    session: Status | None = None  # a live Claude session on `branch`


@dataclass(frozen=True)
class Bullet:
    text: str
    refs: tuple[int, ...]  # the PRs (or issues closed without one) it covers
    url: str | None  # its first ref's


@dataclass(frozen=True)
class Done:
    bullets: tuple[Bullet, ...]
    note: str | None  # the dim line: why it's a plain list, or NOTHING_DONE


@dataclass(frozen=True)
class Band:
    name: str
    path: str
    repo: str | None
    todo: tuple[Card, ...]
    onit: tuple[Card, ...]
    done: Done
    fetched_at: float | None
    error: str | None


@dataclass(frozen=True)
class Board:
    bands: tuple[Band, ...]
    built_at: float


def issue_number(branch: str) -> int | None:
    """The issue a branch is for: the number starting its name or its last `/` part, followed
    by `-`, `_` or the end (`68-pm-view`, `feat/68-pm-view`)."""
    found = _NUMBER.match(branch.rsplit("/", 1)[-1])
    number = int(found.group(1)) if found else 0
    return number or None


def most_urgent(states: Sequence[Status]) -> Status | None:
    return max(states, key=_URGENCY.__getitem__, default=None)


def _claims(pr: PullRequest) -> set[int]:
    named = issue_number(pr.branch)
    return set(pr.closes) | ({named} if named else set())


def _issue_card(
    issue: Issue,
    local: Mapping[int, str],
    prs: Sequence[PullRequest],
    sessions: Mapping[str, Status],
) -> Card:
    mine = [p for p in prs if issue.number in _claims(p)]
    branch = local.get(issue.number) or (mine[0].branch if mine else None)
    pr = min((p.number for p in mine), default=None)
    session = sessions.get(branch) if branch else None
    return Card(
        "issue",
        issue.number,
        issue.title,
        issue.labels,
        issue.updated_at,
        issue.url,
        branch,
        pr,
        session,
    )


def _pr_card(pr: PullRequest, sessions: Mapping[str, Status]) -> Card:
    return Card(
        "pr",
        pr.number,
        pr.title,
        pr.labels,
        pr.updated_at,
        pr.url,
        pr.branch,
        pr.number,
        sessions.get(pr.branch),
    )


def _newest_first(cards: Sequence[Card]) -> tuple[Card, ...]:
    return tuple(sorted(cards, key=lambda c: (-c.updated_at, -c.number)))


def _by_number(branches: Sequence[str]) -> dict[int, str]:
    """Each issue number's first branch."""
    found: dict[int, str] = {}
    for branch in branches:
        number = issue_number(branch)
        if number is not None:
            found.setdefault(number, branch)
    return found


def build_band(
    name: str,
    path: str,
    repo: str | None,
    data: RepoData | None,
    local: Sequence[str],
    sessions: Mapping[str, Status],
    done: Done,
    fetched_at: float | None,
    error: str | None,
) -> Band:
    if repo is None:
        return Band(name, path, None, (), (), done, None, NOT_ON_GITHUB)
    if data is None:
        return Band(name, path, repo, (), (), done, fetched_at, error)
    by_number = _by_number(local)
    open_numbers = {i.number for i in data.issues}
    claimed = set(by_number).union(*(_claims(p) for p in data.prs))
    cards = [_issue_card(i, by_number, data.prs, sessions) for i in data.issues]
    onit = [c for c in cards if c.number in claimed]
    onit += [_pr_card(p, sessions) for p in data.prs if not _claims(p) & open_numbers]
    todo = [c for c in cards if c.number not in claimed]
    return Band(name, path, repo, _newest_first(todo), _newest_first(onit), done, fetched_at, error)


def board_to_json(board: Board) -> dict[str, object]:
    return asdict(board)


def _label(d: Mapping[str, Any]) -> Label:
    return Label(str(d["name"]), str(d["color"]))


def _card(d: Mapping[str, Any]) -> Card:
    if d["kind"] not in ("issue", "pr") or d["session"] not in (None, *_URGENCY):
        raise ValueError(d["kind"])
    return Card(
        d["kind"],
        int(d["number"]),
        str(d["title"]),
        tuple(_label(x) for x in d["labels"]),
        float(d["updated_at"]),
        str(d["url"]),
        _opt(d["branch"]),
        None if d["pr"] is None else int(d["pr"]),
        d["session"],
    )


def _opt(v: object) -> str | None:
    return None if v is None else str(v)


def _band(d: Mapping[str, Any]) -> Band:
    if not isinstance(d["name"], str):
        raise ValueError("name")
    done = d["done"]
    bullets = tuple(
        Bullet(str(b["text"]), tuple(int(r) for r in b["refs"]), _opt(b["url"]))
        for b in done["bullets"]
    )
    return Band(
        d["name"],
        str(d["path"]),
        _opt(d["repo"]),
        tuple(_card(c) for c in d["todo"]),
        tuple(_card(c) for c in d["onit"]),
        Done(bullets, _opt(done["note"])),
        None if d["fetched_at"] is None else float(d["fetched_at"]),
        _opt(d["error"]),
    )


def board_from_json(data: object) -> Board | None:
    """The board a file holds; None when it isn't one (an older or broken file)."""
    try:
        if not isinstance(data, dict) or not isinstance(data["bands"], list):
            return None
        return Board(tuple(_band(b) for b in data["bands"]), float(data["built_at"]))
    except (KeyError, TypeError, ValueError):
        return None
