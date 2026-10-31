"""When the PM view reads GitHub (#68), and the board it keeps.

Runs on a thread of the deck only: GitHub is read when the deck starts (if the cache is an hour
old), every hour, on `R`, and shortly after a Claude session ran a command that changes GitHub
(seen in the hook events cardak already stores). Local branches are read from git every few
seconds, without asking GitHub. Each repository's last fetch and done column are cached under
`~/.cache/cardak/github/`; the board goes to `~/.local/state/cardak/pm.json` for the phone page.
"""

import contextlib
import json
import os
import re
import tempfile
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from cardak.collectors import git
from cardak.collectors.github import (
    Closed,
    GithubError,
    Issue,
    Label,
    Merged,
    PullRequest,
    RepoData,
    repo_from_url,
)
from cardak.collectors.hooks import parse
from cardak.core.agents import Status
from cardak.core.config import Config, Project, cache_dir, state_dir
from cardak.core.pm import (
    Band,
    Board,
    Bullet,
    Done,
    board_from_json,
    board_to_json,
    build_band,
    most_urgent,
)
from cardak.core.pm_done import done_hash, write_done
from cardak.core.store import Store, StoreError
from cardak.core.summary import ProviderTools

REFRESH_EVERY = 3600.0
ERROR_RETRY = 300.0  # after a failed fetch (offline, logged out); `R` is always sooner
TRIGGER_DELAY = 15.0  # after a GitHub-changing command: a push is often followed by `gh pr create`
MIN_GAP = 60.0  # between two fetches of one repository, whatever asked
LOCAL_EVERY = 10.0
EVENTS_EVERY = 5.0
TICK = 1.0
DONE_DAYS = 7
_GH = re.compile(r"\bgh\s+(issue|pr|release)\b|\bgit\s+push\b")

Fetch = Callable[[str, float], RepoData]
Observed = tuple[Path, Path | None, Status]  # (project path, session cwd, its state)


def board_path(env: Mapping[str, str] | None = None) -> Path:
    return state_dir(env) / "pm.json"


def repo_cache_dir(env: Mapping[str, str] | None = None) -> Path:
    return cache_dir(env) / "github"


def changes_github(payload: str) -> bool:
    """Whether a stored `PostToolUse` payload ran something that changes GitHub."""
    event = parse(payload)
    if event is None or event.tool_name is None:
        return False
    if event.tool_name.startswith("mcp__github"):
        return True
    command = event.tool_input.get("command")
    return event.tool_name == "Bash" and isinstance(command, str) and bool(_GH.search(command))


@dataclass
class _Repo:
    data: RepoData | None = None
    fetched_at: float | None = None
    error: str | None = None
    done: Done = field(default_factory=lambda: Done((), None))
    done_hash: str | None = None  # set only for a settled done column
    due: float = 0.0
    attempted: float | None = None


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _labels(items: Sequence[Mapping[str, Any]]) -> tuple[Label, ...]:
    return tuple(Label(str(x["name"]), str(x["color"])) for x in items)


def _repo_data(d: Mapping[str, Any]) -> RepoData:
    return RepoData(
        tuple(
            Issue(i["number"], i["title"], _labels(i["labels"]), i["updated_at"], i["url"])
            for i in d["issues"]
        ),
        tuple(
            PullRequest(
                p["number"],
                p["title"],
                p["branch"],
                _labels(p["labels"]),
                p["updated_at"],
                p["url"],
                tuple(p["closes"]),
            )
            for p in d["prs"]
        ),
        tuple(
            Merged(m["number"], m["title"], m["url"], m["merged_at"], tuple(m["closes"]))
            for m in d["merged"]
        ),
        tuple(Closed(c["number"], c["title"], c["url"], c["closed_at"]) for c in d["closed"]),
    )


def _done(d: Mapping[str, Any]) -> Done:
    return Done(
        tuple(Bullet(b["text"], tuple(b["refs"]), b["url"]) for b in d["bullets"]), d["note"]
    )


def _load(path: Path) -> _Repo | None:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return _Repo(
            data=None if d["data"] is None else _repo_data(d["data"]),
            fetched_at=d["fetched_at"],
            error=d["error"],
            done=_done(d["done"]),
            done_hash=d["done_hash"],
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _dump(repo: _Repo) -> str:
    return json.dumps(
        {
            "data": None if repo.data is None else asdict(repo.data),
            "fetched_at": repo.fetched_at,
            "error": repo.error,
            "done": asdict(repo.done),
            "done_hash": repo.done_hash,
        }
    )


class PmService:
    def __init__(
        self,
        config: Callable[[], Config],
        store: Store,
        *,
        keys_dir: Path,
        tools: ProviderTools | None,
        fetch: Fetch,
        git_runner: git.Runner,
        board_file: Path,
        cache: Path,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._config = config
        self._store = store
        self._keys_dir = keys_dir
        self._tools = tools
        self._fetch = fetch
        self._git = git_runner
        self._board_file = board_file
        self._cache = cache
        self._clock = clock
        self._lock = threading.Lock()
        self._observed: list[Observed] = []
        self._asked: list[Path | None] = []
        self._board: Board | None = None
        self._repos: dict[str, _Repo] = {}
        self._repo_of: dict[Path, str | None] = {}
        self._local: dict[Path, tuple[str, ...]] = {}
        self._sessions: dict[Path, dict[str, Status]] = {}
        self._local_at: float | None = None
        self._events_at: float | None = None
        self._watermark = 0

    def observe(self, sessions: Sequence[Observed]) -> None:
        with self._lock:
            self._observed = list(sessions)

    def refresh(self, project: Path | None = None) -> None:
        """`R`: one project's repository, or every one, as soon as `MIN_GAP` allows."""
        with self._lock:
            self._asked.append(project)

    def board(self) -> Board | None:
        with self._lock:
            return self._board

    def step(self) -> None:
        now = self._clock()
        projects = self._config().projects
        if self._local_at is None or now - self._local_at >= LOCAL_EVERY:
            self._local_at = now
            self._read_local(projects)
        if self._events_at is None or now - self._events_at >= EVENTS_EVERY:
            self._events_at = now
            self._read_events(projects)
        self._take_requests(now)
        for name, repo in self._repos.items():
            if repo.due <= now:
                self._fetch_one(name, repo, now)
        self._publish(projects, now)

    def _read_local(self, projects: Sequence[Project]) -> None:
        for p in projects:
            self._read_project(p)
        with self._lock:
            observed = list(self._observed)
        self._read_sessions(observed)

    def _read_project(self, p: Project) -> None:
        self._local[p.path] = git.branches(p.path, self._git) or ()
        remote = git.remote_url(p.path, self._git)
        repo = p.github or (repo_from_url(remote) if remote else None)
        self._repo_of[p.path] = repo
        if repo is not None and repo not in self._repos:
            self._repos[repo] = self._cached(repo)

    def _read_sessions(self, observed: Sequence[Observed]) -> None:
        """Each project's branches with a Claude session on them, and its most urgent state."""
        sessions: dict[Path, dict[str, list[Status]]] = {}
        for project, cwd, status in observed:
            branch = git.branch(cwd, self._git) if cwd is not None else None
            if branch is not None:
                sessions.setdefault(project, {}).setdefault(branch, []).append(status)
        self._sessions = {
            p: {b: s for b, states in m.items() if (s := most_urgent(states))}
            for p, m in sessions.items()
        }

    def _cached(self, name: str) -> _Repo:
        repo = _load(self._cache / f"{name.replace('/', '__')}.json") or _Repo()
        if repo.fetched_at is not None:
            repo.due = repo.fetched_at + REFRESH_EVERY
            repo.attempted = repo.fetched_at
        return repo

    def _project_of(self, cwd: Path, projects: Sequence[Project]) -> Project | None:
        inside = [p for p in projects if cwd == p.path or p.path in cwd.parents]
        return max(inside, key=lambda p: len(p.path.parts), default=None)

    def _read_events(self, projects: Sequence[Project]) -> None:
        try:
            events = self._store.tool_events_after(self._watermark)
        except StoreError:
            return
        for rowid, ts, payload in events:
            self._watermark = rowid
            repo = self._changed(payload, projects)
            if repo is not None and ts > (repo.fetched_at or 0.0):
                repo.due = min(repo.due, self._allowed(repo, ts + TRIGGER_DELAY))

    def _changed(self, payload: str, projects: Sequence[Project]) -> "_Repo | None":
        """The repository a stored event changed on GitHub, if it did."""
        event = parse(payload)
        if event is None or event.cwd is None or not changes_github(payload):
            return None
        project = self._project_of(event.cwd, projects)
        name = self._repo_of.get(project.path) if project else None
        return self._repos.get(name) if name else None

    def _allowed(self, repo: _Repo, when: float) -> float:
        return when if repo.attempted is None else max(when, repo.attempted + MIN_GAP)

    def _take_requests(self, now: float) -> None:
        with self._lock:
            asked, self._asked = self._asked, []
        for project in asked:
            names = set(self._repo_of.values()) if project is None else {self._repo_of.get(project)}
            for name in names:
                if name is not None and name in self._repos:
                    repo = self._repos[name]
                    repo.due = min(repo.due, self._allowed(repo, now))

    def _fetch_one(self, name: str, repo: _Repo, now: float) -> None:
        repo.attempted = now
        try:
            data = self._fetch(name, now - DONE_DAYS * 86_400)
        except GithubError as e:
            repo.error = str(e)
            repo.due = now + ERROR_RETRY
            self._save(name, repo)
            return
        repo.data, repo.fetched_at, repo.error, repo.due = data, now, None, now + REFRESH_EVERY
        if repo.done_hash is None or repo.done_hash != done_hash(data):
            try:
                done, settled = write_done(
                    name,
                    data,
                    now,
                    summary=self._config().summary,
                    keys_dir=self._keys_dir,
                    tools=self._tools,
                    store=self._store,
                )
            except StoreError:
                done, settled = repo.done, False
            repo.done, repo.done_hash = done, done_hash(data) if settled else None
        self._save(name, repo)

    def _save(self, name: str, repo: _Repo) -> None:
        with contextlib.suppress(OSError):  # a convenience: the next fetch rewrites it
            _write(self._cache / f"{name.replace('/', '__')}.json", _dump(repo))

    def _publish(self, projects: Sequence[Project], now: float) -> None:
        bands = tuple(self._band(p) for p in projects)
        with self._lock:
            if self._board is not None and self._board.bands == bands:
                return
            self._board = Board(bands, now)
            board = self._board
        with contextlib.suppress(OSError):  # the phone keeps the last one; the deck has this
            _write(self._board_file, json.dumps(board_to_json(board)))

    def _band(self, p: Project) -> Band:
        name = self._repo_of.get(p.path)
        repo = self._repos.get(name) if name else None
        return build_band(
            p.name,
            str(p.path),
            name,
            repo.data if repo else None,
            self._local.get(p.path, ()),
            self._sessions.get(p.path, {}),
            repo.done if repo else Done((), None),
            repo.fetched_at if repo else None,
            repo.error if repo else None,
        )


class BoardFile:
    """The board the deck last wrote, re-read when the file changes (the daemon's `/api/pm`)."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._seen: tuple[tuple[int, int], Board | None] | None = None

    def get(self) -> Board | None:
        try:
            st = self._path.stat()
        except OSError:
            return None
        stamp = (st.st_mtime_ns, st.st_size)
        if self._seen is None or self._seen[0] != stamp:
            try:
                board = board_from_json(json.loads(self._path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                board = None
            self._seen = (stamp, board)
        return self._seen[1]


def background(service: PmService, log: Callable[[str], None]) -> threading.Event:
    """Step the service every `TICK` on a daemon thread until the returned event is set."""
    stop = threading.Event()

    def loop() -> None:
        while not stop.is_set():
            try:
                service.step()
            except Exception as e:  # the PM view never takes the deck down
                log(f"pm view: {e!r}")
            stop.wait(TICK)

    threading.Thread(target=loop, name="cardak-pm", daemon=True).start()
    return stop
