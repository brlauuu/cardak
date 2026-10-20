import subprocess
from pathlib import Path

import pytest

from cardak.config import Config, Project
from cardak.resolver import OTHER, ProjectResolver, Resolved, run_git


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
    from cardak.config import load

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
