import os
import subprocess
from pathlib import Path

import pytest

from cardak.collectors.git import Commit, GitState, branch, branches, remote_url, state, toplevel

T0 = 1792000000  # commits at T0, T0 + 100, T0 + 200


@pytest.fixture(autouse=True)
def git_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "test@example.com")


def git(repo: Path, *args: str, ts: int | None = None) -> str:
    env = None
    if ts is not None:
        env = {**os.environ, "GIT_AUTHOR_DATE": f"@{ts}", "GIT_COMMITTER_DATE": f"@{ts}"}
    out = subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env
    )
    return out.stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "webshop"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    for i, name in enumerate(("one", "two", "three")):
        (r / name).write_text(name)
        git(r, "add", name)
        git(r, "commit", "-q", "-m", f"add {name}", ts=T0 + 100 * i)
    return r


def test_toplevel_of_subdir(repo: Path) -> None:
    (repo / "src").mkdir()
    assert toplevel(repo / "src") == repo.resolve()


def test_toplevel_of_worktree_is_its_own(repo: Path, tmp_path: Path) -> None:
    wt = tmp_path / "webshop-feature"
    git(repo, "worktree", "add", "-q", "-b", "feature", str(wt))
    assert toplevel(wt) == wt.resolve()


def test_toplevel_outside_git_is_none(tmp_path: Path) -> None:
    assert toplevel(tmp_path) is None


def test_toplevel_missing_dir_is_none(tmp_path: Path) -> None:
    assert toplevel(tmp_path / "gone") is None


def test_state_counts_changed_and_untracked(repo: Path) -> None:
    (repo / "one").write_text("changed")
    (repo / "new1").write_text("x")
    (repo / "new2").write_text("x")
    s = state(repo, None)
    assert s == GitState(branch="main", changed=1, untracked=2, commits=())


def test_log_since_newest_first_and_limit(repo: Path) -> None:
    s = state(repo, T0 + 50)
    assert s is not None
    assert [c.subject for c in s.commits] == ["add three", "add two"]
    assert s.commits[0].ts == T0 + 200 and len(s.commits[0].sha) >= 7
    limited = state(repo, T0 - 10, limit=1)
    assert limited is not None and [c.subject for c in limited.commits] == ["add three"]


def test_subject_with_tabs_and_brackets(repo: Path) -> None:
    (repo / "four").write_text("4")
    git(repo, "add", "four")
    git(repo, "commit", "-q", "-m", "fix [b]x\twith a tab", ts=T0 + 300)
    s = state(repo, T0 + 250)
    assert s is not None and s.commits == (
        Commit(s.commits[0].sha, T0 + 300, "fix [b]x\twith a tab"),
    )


def test_detached_branch_is_none(repo: Path) -> None:
    git(repo, "checkout", "-q", "--detach")
    s = state(repo, None)
    assert s is not None and s.branch is None


def test_not_a_repo_is_none(tmp_path: Path) -> None:
    assert state(tmp_path, None) is None


def test_timeout_is_none(repo: Path) -> None:
    def slow(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired("git", 3)

    assert toplevel(repo, runner=slow) is None
    assert state(repo, None, runner=slow) is None


def test_no_git_is_none(repo: Path) -> None:
    def missing(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("git")

    assert state(repo, None, runner=missing) is None


def test_branches_include_every_worktrees(repo: Path, tmp_path: Path) -> None:
    git(repo, "branch", "68-pm-view")
    git(repo, "worktree", "add", "-q", "-b", "feat/70-limits", str(tmp_path / "wt"))
    assert sorted(branches(repo) or ()) == ["68-pm-view", "feat/70-limits", "main"]
    assert branches(tmp_path / "wt") == branches(repo)


def test_branch_of_a_folder(repo: Path, tmp_path: Path) -> None:
    assert branch(repo / ".") == "main"
    git(repo, "checkout", "-q", "--detach")
    assert branch(repo) is None
    assert branch(tmp_path) is None  # not git


def test_remote_url(repo: Path) -> None:
    assert remote_url(repo) is None
    git(repo, "remote", "add", "origin", "git@github.com:example/webshop.git")
    assert remote_url(repo) == "git@github.com:example/webshop.git"
