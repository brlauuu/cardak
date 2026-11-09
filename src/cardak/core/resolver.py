"""Map a pane's cwd to a project: configured path first, then git root, else Other."""

import subprocess
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from cardak.core.config import Config, real_path

Kind = Literal["config", "git", "other"]
GitLookup = Callable[[Path], Path | None]
RealLookup = Callable[[Path], Path]
LOOKUP_TIMEOUT_S = 0.5


@dataclass(frozen=True)
class Resolved:
    kind: Kind
    key: Path | None
    name: str
    accent: str | None = None
    order: int = 0
    theme: str | None = None


OTHER = Resolved("other", None, "Other")


def run_git(cwd: Path) -> Path | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=1,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    top = out.stdout.strip()
    return Path(top) if out.returncode == 0 and top else None


class Bounded[T]:
    """A filesystem lookup run on a thread and waited for at most `timeout` (#111). A hung
    network mount blocks a stat, or git and the wait after killing it, in the kernel, where no
    timeout of its own reaches. Until a lookup that ran over returns, asking again answers None
    at once and starts nothing; its answer is kept once it comes."""

    def __init__(self, lookup: Callable[[Path], T], timeout: float) -> None:
        self._lookup = lookup
        self._timeout = timeout
        self._done: dict[Path, T] = {}
        self._pending: set[Path] = set()
        self._lock = threading.Lock()

    def __call__(self, path: Path) -> tuple[T] | None:
        """(the answer,), or None while the lookup hasn't returned."""
        with self._lock:
            if path in self._done:
                return (self._done[path],)
            if path in self._pending:
                return None
            self._pending.add(path)
        thread = threading.Thread(target=self._run, args=(path,), name="cardak-lookup", daemon=True)
        thread.start()
        thread.join(self._timeout)
        with self._lock:
            return (self._done[path],) if path in self._done else None

    def _run(self, path: Path) -> None:
        try:
            found = self._lookup(path)
        except Exception:  # asked again on the next poll
            with self._lock:
                self._pending.discard(path)
            return
        with self._lock:
            self._done[path] = found
            self._pending.discard(path)


class ProjectResolver:
    def __init__(
        self,
        config: Config,
        git: GitLookup = run_git,
        real: RealLookup = real_path,
        timeout: float = LOOKUP_TIMEOUT_S,
    ) -> None:
        self._git = Bounded(git, timeout)
        self._real = Bounded(real, timeout)
        # Each project under its configured path and, if it's a symlink, the real one too: a
        # pane's cwd can come from /proc (resolved) or OSC 7 (the shell's logical path) (#14).
        entries = [
            (match, order, project)
            for order, project in enumerate(config.projects)
            for match in dict.fromkeys((project.path, self.real(project.path)))
        ]
        # Longest path first so a nested project wins over its parent.
        self._projects = sorted(entries, key=lambda e: len(e[0].parts), reverse=True)

    def _configured(self, cwd: Path) -> Resolved | None:
        for match, order, project in self._projects:
            if cwd.is_relative_to(match):
                return Resolved(
                    "config", project.path, project.name, project.accent, order, project.theme
                )
        return None

    def real(self, path: Path) -> Path:
        """path with symlinks resolved, remembered for as long as this resolver (a config); as
        given while the lookup hangs."""
        found = self._real(path)
        return path if found is None else found[0]

    def resolve(self, cwd: Path | None) -> Resolved:
        if cwd is None:
            return OTHER
        found = self._configured(cwd)
        if found is None and (real := self.real(cwd)) != cwd:
            found = self._configured(real)
        if found is not None:
            return found
        found = self._git(cwd)
        root = None if found is None else found[0]
        return OTHER if root is None else Resolved("git", root, root.name)


class Warmer:
    """Asks a resolver about many folders on a thread of its own, so the poll never waits on git
    for folders no pane is in: those of the month's spend, trends and context (#114). One pass
    at a time; `ready` waits a moment for it, enough for folders already asked."""

    def __init__(self, wait: float = 0.05) -> None:
        self._wait = wait
        self._lock = threading.Lock()
        self._done: tuple[object, object] | None = None
        self._running: tuple[object, object] | None = None
        self._thread: threading.Thread | None = None

    def ready(
        self, resolver: ProjectResolver, key: object, folders: Callable[[], Iterable[str | None]]
    ) -> bool:
        """Whether resolver has answered every folder of key (compared by identity), starting
        a pass for them when none is running."""
        token = (resolver, key)
        with self._lock:
            if _same(self._done, token):
                return True
            if self._thread is None or not self._thread.is_alive():
                self._running = token
                self._thread = threading.Thread(
                    target=self._warm, args=(token, list(folders())), name="cardak-warm",
                    daemon=True,
                )  # fmt: skip
                self._thread.start()
            thread, running = self._thread, self._running
        if _same(running, token):
            thread.join(self._wait)
        with self._lock:
            return _same(self._done, token)

    def _warm(self, token: tuple[ProjectResolver, object], folders: list[str | None]) -> None:
        try:
            for cwd in folders:
                token[0].resolve(Path(cwd) if cwd else None)
        except Exception:  # raised again where the poll groups them; nothing printed here
            pass
        with self._lock:
            self._done = token


def _same(a: tuple[object, object] | None, b: tuple[object, object]) -> bool:
    return a is not None and a[0] is b[0] and a[1] is b[1]
