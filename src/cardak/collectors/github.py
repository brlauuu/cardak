"""GitHub through `gh api graphql`, read-only: a repository's open issues and PRs, and what was
merged or closed since a time (#68). `gh` owns authentication; cardak handles no token.

`gh pr list --json` has no closing issue references (checked with gh 2.45), GraphQL does.
Every failure is a `GithubError` whose text is one line for the deck.
"""

import json
import re
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

Runner = Callable[..., subprocess.CompletedProcess[str]]
TIMEOUT = 20.0
PAGE = 100
NOT_INSTALLED = "gh isn't installed"
OFFLINE = "can't reach GitHub"
ODD = "unexpected reply from GitHub"
_OFFLINE_HINTS = ("error connecting", "could not resolve host", "connection", "timeout", "dial tcp")
_REPO_URL = re.compile(
    r"^(?:git@github\.com:|https://github\.com/|ssh://git@github\.com/)"
    r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)

_LABELS = "labels(first: 20) { nodes { name color } }"
_CLOSES = "closingIssuesReferences(first: 10) { nodes { number } }"
_PAGE = "pageInfo { hasNextPage endCursor }"
_ORDER = "orderBy: {field: UPDATED_AT, direction: DESC}"
_ISSUES = (
    f"issues(states: OPEN, first: {PAGE}, after: $after, " + _ORDER + ")"
    " { nodes { number title url updatedAt " + _LABELS + " } " + _PAGE + " }"
)
_PRS = (
    f"pullRequests(states: OPEN, first: {PAGE}, after: $after, " + _ORDER + ")"
    " { nodes { number title url updatedAt headRefName "
    + _LABELS
    + " "
    + _CLOSES
    + " } "
    + _PAGE
    + " }"
)
_DONE = (
    "merged: pullRequests(states: MERGED, first: 50, " + _ORDER + ")"
    " { nodes { number title url mergedAt " + _CLOSES + " } }"
    " closed: issues(states: CLOSED, first: 50, " + _ORDER + ")"
    " { nodes { number title url closedAt stateReason } }"
)
_HEAD = (
    "query($owner: String!, $name: String!, $after: String)"
    " { repository(owner: $owner, name: $name) {"
)


@dataclass(frozen=True)
class Label:
    name: str
    color: str  # GitHub's hex, without '#'


@dataclass(frozen=True)
class Issue:
    number: int
    title: str
    labels: tuple[Label, ...]
    updated_at: float
    url: str


@dataclass(frozen=True)
class PullRequest:
    number: int
    title: str
    branch: str
    labels: tuple[Label, ...]
    updated_at: float
    url: str
    closes: tuple[int, ...]


@dataclass(frozen=True)
class Merged:
    number: int
    title: str
    url: str
    merged_at: float
    closes: tuple[int, ...]


@dataclass(frozen=True)
class Closed:
    number: int
    title: str
    url: str
    closed_at: float


@dataclass(frozen=True)
class RepoData:
    issues: tuple[Issue, ...]
    prs: tuple[PullRequest, ...]
    merged: tuple[Merged, ...]
    closed: tuple[Closed, ...]


class GithubError(Exception):
    pass


def repo_from_url(url: str) -> str | None:
    """`owner/repo` of a github.com remote URL (ssh or https); None for any other host."""
    found = _REPO_URL.match(url.strip())
    return f"{found.group(1)}/{found.group(2)}" if found else None


def _ts(value: object) -> float:
    if not isinstance(value, str):
        raise GithubError(ODD)
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def _nodes(conn: object) -> list[Mapping[str, Any]]:
    if not isinstance(conn, dict) or not isinstance(conn.get("nodes"), list):
        raise GithubError(ODD)
    return [n for n in conn["nodes"] if isinstance(n, dict)]


def _labels(node: Mapping[str, Any]) -> tuple[Label, ...]:
    return tuple(Label(str(n["name"]), str(n.get("color") or "")) for n in _nodes(node["labels"]))


def _closes(node: Mapping[str, Any]) -> tuple[int, ...]:
    return tuple(int(n["number"]) for n in _nodes(node["closingIssuesReferences"]))


def _failure(out: subprocess.CompletedProcess[str]) -> GithubError:
    try:
        errors = json.loads(out.stdout).get("errors")
        if errors and isinstance(errors[0].get("message"), str):
            return GithubError(" ".join(errors[0]["message"].split()))
    except (ValueError, AttributeError, TypeError, IndexError):
        pass
    lines = [line.strip() for line in out.stderr.splitlines() if line.strip()]
    if not lines:
        return GithubError(f"gh exited {out.returncode}")
    if any(hint in lines[0].lower() for hint in _OFFLINE_HINTS):
        return GithubError(OFFLINE)
    return GithubError(lines[0])


def _query(
    repo: str, body: str, after: str | None, run: Runner, timeout: float
) -> Mapping[str, Any]:
    owner, name = repo.split("/", 1)
    argv = ["gh", "api", "graphql", "-f", f"query={_HEAD} {body} }} }}"]
    argv += ["-F", f"owner={owner}", "-F", f"name={name}"]
    if after is not None:
        argv += ["-F", f"after={after}"]
    try:
        out = run(argv, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as e:
        raise GithubError(NOT_INSTALLED) from e
    except subprocess.TimeoutExpired as e:
        raise GithubError(OFFLINE) from e
    except OSError as e:
        raise GithubError(str(e)) from e
    if out.returncode != 0:
        raise _failure(out)
    try:
        repository = json.loads(out.stdout)["data"]["repository"]
    except (ValueError, KeyError, TypeError) as e:
        raise GithubError(ODD) from e
    if not isinstance(repository, dict):
        raise GithubError(ODD)
    return repository


def _rest(
    repo: str, key: str, body: str, first: Mapping[str, Any], run: Runner, timeout: float
) -> list[Mapping[str, Any]]:
    """Every node of connection `key`: the first page, then one query per further page."""
    conn = first[key]
    nodes = _nodes(conn)
    while isinstance(conn, dict) and (conn.get("pageInfo") or {}).get("hasNextPage"):
        after = conn["pageInfo"].get("endCursor")
        conn = _query(repo, body, str(after), run, timeout)[key]
        nodes += _nodes(conn)
    return nodes


def fetch(
    repo: str, since: float, run: Runner = subprocess.run, timeout: float = TIMEOUT
) -> RepoData:
    first = _query(repo, f"{_ISSUES} {_PRS} {_DONE}", None, run, timeout)
    try:
        issues = tuple(
            Issue(int(n["number"]), str(n["title"]), _labels(n), _ts(n["updatedAt"]), str(n["url"]))
            for n in _rest(repo, "issues", _ISSUES, first, run, timeout)
        )
        prs = tuple(
            PullRequest(
                int(n["number"]),
                str(n["title"]),
                str(n["headRefName"]),
                _labels(n),
                _ts(n["updatedAt"]),
                str(n["url"]),
                _closes(n),
            )
            for n in _rest(repo, "pullRequests", _PRS, first, run, timeout)
        )
        merged = tuple(
            Merged(int(n["number"]), str(n["title"]), str(n["url"]), _ts(n["mergedAt"]), _closes(n))
            for n in _nodes(first["merged"])
            if n.get("mergedAt") and _ts(n["mergedAt"]) >= since
        )
        closed = tuple(
            Closed(int(n["number"]), str(n["title"]), str(n["url"]), _ts(n["closedAt"]))
            for n in _nodes(first["closed"])
            if n.get("closedAt")
            and n.get("stateReason") != "NOT_PLANNED"
            and _ts(n["closedAt"]) >= since
        )
    except (KeyError, TypeError, ValueError) as e:
        raise GithubError(ODD) from e
    return RepoData(issues, prs, merged, closed)
