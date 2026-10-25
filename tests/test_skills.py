import re
from pathlib import Path

import pytest

from cardak.claude import skills
from cardak.claude.skills import MARKER, NAMES, SkillsError, bundled, hint, install, status
from cardak.collectors.handoff import parse_entries


def frontmatter(text: str) -> tuple[list[str], list[str]]:
    lines = text.splitlines()
    assert lines[0] == "---"
    end = lines.index("---", 1)
    return lines[1:end], lines[end + 1 :]


@pytest.mark.parametrize("name", NAMES)
def test_bundled_frontmatter_and_marker(name: str) -> None:
    head, rest = frontmatter(bundled(name))
    assert f"name: {name}" in head
    assert any(line.startswith("description: ") for line in head)
    assert rest[0] == MARKER


def test_checkpoint_example_parses() -> None:
    text = bundled("checkpoint")
    [block] = re.findall(r"```markdown\n(.*?)```", text, re.S)
    [entry] = parse_entries(block)
    assert entry.branch is not None
    assert "**Next step:**" in entry.body


def test_checkpoint_mentions_rules() -> None:
    text = bundled("checkpoint")
    for needle in (".cardak/handoff.md", ".cardak/.gitignore", "--show-toplevel", "$ARGUMENTS"):
        assert needle in text
    assert "never commit" in text.lower()
    assert "# Handoff notes (written by /checkpoint, read by /where-were-we and cardak)" in text


def test_where_were_we_mentions_sources() -> None:
    text = bundled("where-were-we")
    for needle in ("git log", "git status", "gh ", ".cardak/handoff.md", "--show-toplevel"):
        assert needle in text


def test_install_fresh_then_unchanged(tmp_path: Path) -> None:
    written = install(tmp_path, force=False)
    assert written == [tmp_path / n / "SKILL.md" for n in NAMES]
    assert all(status(tmp_path, n) == "installed" for n in NAMES)
    assert (tmp_path / "checkpoint" / "SKILL.md").read_text() == bundled("checkpoint")
    assert install(tmp_path, force=False) == []


def test_install_refuses_foreign_writes_nothing(tmp_path: Path) -> None:
    mine = tmp_path / "checkpoint" / "SKILL.md"
    mine.parent.mkdir()
    mine.write_text("---\nname: checkpoint\n---\nmy own\n")
    with pytest.raises(SkillsError, match=str(mine)):
        install(tmp_path, force=False)
    assert mine.read_text().endswith("my own\n")
    assert status(tmp_path, "where-were-we") == "missing"
    assert status(tmp_path, "checkpoint") == "foreign"


def test_install_force_replaces_foreign(tmp_path: Path) -> None:
    mine = tmp_path / "checkpoint" / "SKILL.md"
    mine.parent.mkdir()
    mine.write_text("my own\n")
    install(tmp_path, force=True)
    assert status(tmp_path, "checkpoint") == "installed"


def test_outdated_status(tmp_path: Path) -> None:
    install(tmp_path, force=False)
    old = tmp_path / "where-were-we" / "SKILL.md"
    old.write_text(old.read_text() + "\nan older version\n")
    assert status(tmp_path, "where-were-we") == "outdated"
    assert install(tmp_path, force=False) == [old]
    assert status(tmp_path, "where-were-we") == "installed"


def test_install_keeps_mode(tmp_path: Path) -> None:
    install(tmp_path, force=False)
    path = tmp_path / "checkpoint" / "SKILL.md"
    assert path.stat().st_mode & 0o777 == 0o644


def test_uninstall_leaves_foreign_and_other_files(tmp_path: Path) -> None:
    install(tmp_path, force=False)
    extra = tmp_path / "checkpoint" / "notes.txt"
    extra.write_text("keep")
    foreign = tmp_path / "where-were-we" / "SKILL.md"
    foreign.write_text("mine")
    assert skills.uninstall(tmp_path) == [tmp_path / "checkpoint" / "SKILL.md"]
    assert extra.exists() and foreign.read_text() == "mine"
    install(tmp_path, force=True)
    extra.unlink()
    skills.uninstall(tmp_path)
    assert not (tmp_path / "checkpoint").exists() and not (tmp_path / "where-were-we").exists()


def test_hint(tmp_path: Path) -> None:
    assert hint(tmp_path) == (
        "Run `cardak skills install` to add /checkpoint and /where-were-we to Claude Code."
    )
    install(tmp_path, force=False)
    assert hint(tmp_path) is None
    (tmp_path / "checkpoint" / "SKILL.md").write_text("someone else's")  # foreign counts
    assert hint(tmp_path) is None


def test_default_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    assert skills.default_dir() == tmp_path / ".claude" / "skills"
