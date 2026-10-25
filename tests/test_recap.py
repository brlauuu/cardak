import os
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from cardak.collectors.handoff import NOTE
from cardak.core.recap import Note, ago, dirs_for, notes, since
from cardak.core.snapshot import Group, PaneRow

TS = datetime(2026, 10, 24, 14, 10).timestamp()


@pytest.fixture(autouse=True)
def git_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "test@example.com")


def git(repo: Path, *args: str, ts: float | None = None) -> None:
    env = dict(os.environ)
    if ts is not None:
        env |= {"GIT_AUTHOR_DATE": f"@{int(ts)}", "GIT_COMMITTER_DATE": f"@{int(ts)}"}
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


def commit(repo: Path, name: str, ts: float) -> None:
    (repo / name).write_text(name)
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", f"add {name}", ts=ts)


def note(top: Path, *headings: str) -> None:
    (top / NOTE).parent.mkdir(exist_ok=True)
    (top / NOTE).write_text("".join(f"{h}\n\n**Next step:** x\n\n" for h in headings))


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "webshop"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    commit(r, "one", TS - 3600)
    return r


def row(pane_id: int, cwd: Path | None) -> PaneRow:
    return PaneRow(
        pane_id=pane_id, label="zsh", detail=None, cwd=cwd, cwd_display=".", cpu_pct=None,
        rss=0, is_self=False,
    )  # fmt: skip


def group(kind: str, key: Path | None, *rows: PaneRow) -> Group:
    return Group(
        kind=kind, key=key, name="webshop", accent=None, path=None, cpu_pct=0.0, rss=0,  # type: ignore[arg-type]
        panes=rows,
    )  # fmt: skip


def test_dirs_for_pane() -> None:
    r = row(1, Path("/home/user/repos/webshop/src"))
    assert dirs_for(group("config", Path("/home/user/repos/webshop"), r), r) == [r.cwd]


def test_dirs_for_project() -> None:
    a, b = row(1, Path("/a")), row(2, None)
    key = Path("/home/user/repos/webshop")
    assert dirs_for(group("git", key, a, b), None) == [key, Path("/a")]


@pytest.mark.parametrize("kind", ["waiting", "other"])
def test_dirs_for_header(kind: str) -> None:
    assert dirs_for(group(kind, None, row(1, Path("/a"))), None) == "Select a project or a pane."


def test_dirs_for_pane_without_cwd() -> None:
    r = row(1, None)
    assert dirs_for(group("config", Path("/p"), r), r) == "This pane has no directory."


def test_worktree_notes_listed_newest_first(repo: Path, tmp_path: Path) -> None:
    wt = tmp_path / "webshop-feature"
    git(repo, "worktree", "add", "-q", "-b", "feature", str(wt))
    note(repo, "## 2026-10-20 09:05 · main")
    note(wt, "## 2026-10-22 10:00 · feature", "## 2026-10-24 14:10 · feature")
    got = notes([repo, wt])
    assert [n.top for n in got] == [wt.resolve(), repo.resolve()]
    assert got[0].entry is not None and got[0].entry.when == datetime(2026, 10, 24, 14, 10)


def test_same_top_once(repo: Path) -> None:
    (repo / "src").mkdir()
    assert notes([repo, repo / "src"]) == [Note(repo.resolve(), None)]


def test_no_note_place_listed_last(repo: Path, tmp_path: Path) -> None:
    other = tmp_path / "plain"
    other.mkdir()
    note(other, "## 2026-10-20 09:05")
    got = notes([repo, other])
    assert [n.top for n in got] == [other, repo.resolve()]
    assert got[1].entry is None


def test_missing_cwd_is_skipped_gracefully(tmp_path: Path) -> None:
    gone = tmp_path / "gone"
    assert notes([gone]) == [Note(gone, None)]


def test_not_a_repo_since_is_none(tmp_path: Path) -> None:
    note(tmp_path, "## 2026-10-24 14:10")
    [n] = notes([tmp_path])
    assert n.entry is not None and since(n) is None


def test_since_counts_commits_after_entry(repo: Path) -> None:
    note(repo, "## 2026-10-24 14:10 · main")
    commit(repo, "two", TS + 60)
    commit(repo, "three", TS + 120)
    [n] = notes([repo])
    s = since(n)
    assert s is not None and [c.subject for c in s.commits] == ["add three", "add two"]


def test_since_without_entry_lists_no_commits(repo: Path) -> None:
    [n] = notes([repo])
    s = since(n)
    assert s is not None and s.commits == () and s.branch == "main"


@pytest.mark.parametrize(
    ("seconds", "text"),
    [
        (-30, "just now"),
        (59, "just now"),
        (60, "1 minute ago"),
        (125, "2 minutes ago"),
        (3600, "1 hour ago"),
        (7300, "2 hours ago"),
        (86_400, "1 day ago"),
        (3 * 86_400 + 5, "3 days ago"),
    ],
)
def test_ago_forms(seconds: float, text: str) -> None:
    assert ago(seconds) == text
