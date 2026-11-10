import subprocess
from pathlib import Path

import pytest

from cardak.core.config import Config, Project
from cardak.core.resolver import OTHER, ProjectResolver, Resolved, run_git


class FakeGit:
    def __init__(self, roots: dict[Path, Path]) -> None:
        self.roots = roots
        self.calls: list[Path] = []

    def __call__(self, cwd: Path) -> Path | None:
        self.calls.append(cwd)
        return self.roots.get(cwd)


CONFIG = Config(
    projects=(
        Project(name="webshop", path=Path("/r/webshop"), accent="#fabd2f"),
        Project(name="shop-docs", path=Path("/r/webshop/docs")),
        Project(name="a", path=Path("/r/a")),
    )
)


def test_longest_configured_prefix_wins() -> None:
    r = ProjectResolver(CONFIG, git=FakeGit({}))
    assert r.resolve(Path("/r/webshop/docs/api")) == Resolved(
        "config", Path("/r/webshop/docs"), "shop-docs", None, 1
    )
    assert r.resolve(Path("/r/webshop/src")) == Resolved(
        "config", Path("/r/webshop"), "webshop", "#fabd2f", 0
    )
    assert r.resolve(Path("/r/webshop")).name == "webshop"


def test_prefix_matches_whole_components_only() -> None:
    git = FakeGit({Path("/r/ab"): Path("/r/ab")})
    r = ProjectResolver(CONFIG, git=git)
    assert r.resolve(Path("/r/ab")) == Resolved("git", Path("/r/ab"), "ab")


def test_config_path_with_tilde_and_trailing_slash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cardak.core.config import load

    monkeypatch.setenv("HOME", str(tmp_path))
    f = tmp_path / "projects.toml"
    f.write_text('[[project]]\nname = "webshop"\npath = "~/repos/webshop/"\n')
    r = ProjectResolver(load(f), git=FakeGit({}))
    assert r.resolve(tmp_path / "repos/webshop/src").name == "webshop"


def test_git_root_when_not_configured() -> None:
    r = ProjectResolver(CONFIG, git=FakeGit({Path("/x/cardak/src"): Path("/x/cardak")}))
    assert r.resolve(Path("/x/cardak/src")) == Resolved("git", Path("/x/cardak"), "cardak")


def test_other_when_no_repo_or_no_cwd() -> None:
    git = FakeGit({})
    r = ProjectResolver(CONFIG, git=git)
    assert r.resolve(Path("/tmp")) is OTHER
    assert r.resolve(None) is OTHER
    assert git.calls == [Path("/tmp")]


def test_git_lookup_is_cached() -> None:
    git = FakeGit({Path("/x/c"): Path("/x/c")})
    r = ProjectResolver(Config(), git=git)
    r.resolve(Path("/x/c"))
    r.resolve(Path("/x/c"))
    r.resolve(Path("/tmp"))
    r.resolve(Path("/tmp"))
    assert git.calls == [Path("/x/c"), Path("/tmp")]


def test_run_git_finds_toplevel(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "sub").mkdir()
    assert run_git(tmp_path / "sub") == tmp_path.resolve()


def test_run_git_outside_repo_and_missing_dir(tmp_path: Path) -> None:
    assert run_git(tmp_path) is None
    assert run_git(tmp_path / "gone") is None


def test_run_git_missing_binary_or_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", missing)
    assert run_git(Path("/")) is None

    def slow(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired("git", 1)

    monkeypatch.setattr(subprocess, "run", slow)
    assert run_git(Path("/")) is None


def test_symlinked_project_matches_the_real_cwd(tmp_path: Path) -> None:
    real = tmp_path / "disk" / "webshop"
    (real / "src").mkdir(parents=True)
    link = tmp_path / "webshop"
    link.symlink_to(real)
    r = ProjectResolver(Config(projects=(Project(name="webshop", path=link),)), git=FakeGit({}))
    # /proc/<pid>/cwd gives the resolved path (#14); the group keeps the configured one
    assert r.resolve(real / "src") == Resolved("config", link, "webshop")
    assert r.resolve(link / "src").key == link


def test_real_project_matches_a_symlinked_cwd(tmp_path: Path) -> None:
    real = tmp_path / "disk" / "webshop"
    real.mkdir(parents=True)
    link = tmp_path / "webshop"
    link.symlink_to(real)
    git = FakeGit({})
    r = ProjectResolver(Config(projects=(Project(name="webshop", path=real),)), git=git)
    assert r.resolve(link).key == real  # OSC 7 reports the shell's logical path
    assert git.calls == []


def test_missing_project_path_is_harmless(tmp_path: Path) -> None:
    gone = tmp_path / "gone"
    r = ProjectResolver(Config(projects=(Project(name="gone", path=gone),)), git=FakeGit({}))
    assert r.resolve(gone / "x").name == "gone"


class Hung:
    """A lookup stuck on a hung network mount until released (#111)."""

    def __init__(self, result: Path | None) -> None:
        import threading

        self.release = threading.Event()
        self.result = result
        self.calls = 0

    def __call__(self, path: Path) -> Path | None:
        self.calls += 1
        self.release.wait(5)
        return self.result


def test_a_hung_git_lookup_doesnt_stall_the_poll() -> None:
    import time

    git = Hung(Path("/mnt/nas/repo"))
    r = ProjectResolver(Config(), git=git, timeout=0.05)
    start = time.monotonic()
    assert r.resolve(Path("/mnt/nas/repo/src")) == OTHER
    assert time.monotonic() - start < 1
    start = time.monotonic()
    assert r.resolve(Path("/mnt/nas/repo/src")) == OTHER  # still hung: no second wait or call
    assert time.monotonic() - start < 0.04 and git.calls == 1
    git.release.set()
    found = OTHER
    for _ in range(100):  # once the mount answers, the pane gets its repo
        if (found := r.resolve(Path("/mnt/nas/repo/src"))) != OTHER:
            break
        time.sleep(0.01)
    assert found == Resolved("git", Path("/mnt/nas/repo"), "repo")


def test_a_hung_symlink_lookup_doesnt_stall_the_poll() -> None:
    import time

    real = Hung(Path("/r/webshop/src"))
    r = ProjectResolver(
        Config(projects=(Project(name="webshop", path=Path("/r/webshop")),)),
        git=FakeGit({}), real=lambda p: real(p) or p, timeout=0.05,
    )  # fmt: skip
    start = time.monotonic()
    assert r.resolve(Path("/mnt/nas/link")) == OTHER
    assert time.monotonic() - start < 1
    real.release.set()
    found = OTHER
    for _ in range(100):
        if (found := r.resolve(Path("/mnt/nas/link"))) != OTHER:
            break
        time.sleep(0.01)
    assert found.name == "webshop"


def test_warmer_asks_off_the_caller_and_one_pass_at_a_time() -> None:
    import threading
    import time

    from cardak.core.resolver import Warmer

    release = threading.Event()
    asked: list[Path] = []

    def git(cwd: Path) -> Path | None:
        asked.append(cwd)
        release.wait(5)
        return None

    r, w = ProjectResolver(Config(), git=git), Warmer(wait=0.01)
    first, second = object(), object()
    assert not w.ready(r, first, lambda: ["/a", None])
    assert not w.ready(r, second, lambda: ["/b"])  # waits for the running pass, starts none
    release.set()
    deadline = time.monotonic() + 5
    while not w.ready(r, first, lambda: ["/a"]):
        assert time.monotonic() < deadline
        time.sleep(0.01)
    while not w.ready(r, second, lambda: ["/b"]):
        assert time.monotonic() < deadline
        time.sleep(0.01)
    assert asked == [Path("/a"), Path("/b")]


def test_warmer_finishes_a_pass_that_raises() -> None:
    from cardak.core.resolver import Warmer

    def git(cwd: Path) -> Path | None:
        raise RuntimeError("bug")

    r, w, key = ProjectResolver(Config(), git=git), Warmer(wait=1.0), object()
    assert w.ready(r, key, lambda: ["/a"])  # the poll's own grouping raises it again
