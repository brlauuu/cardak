import fcntl
import json
import os
import subprocess
import time
from pathlib import Path

import pytest
from PIL import Image as PilImage

from cardak.core.ascii_art import name_art
from cardak.core.backgrounds import (
    FALLBACK_COLOR,
    PRUNE_AFTER_S,
    Image,
    Paths,
    Tools,
    art_files,
    art_stamp,
    ensure_art,
    find_logos,
    images,
    logo_candidates,
    preview_image,
    read_art,
    record_names,
    render,
)
from cardak.core.config import BackgroundSettings, Config, Project
from cardak.core.themes import Scheme

FIXTURES = Path(__file__).parent / "fixtures"
DARK = Scheme("Dark", "#101010", "#e0e0e0", None, ())
LIGHT = Scheme("Light", "#fafafa", "#202020", None, ())


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    return Paths(tmp_path / "logos", tmp_path / "state" / "backgrounds.json", tmp_path / "cache")


def no_logo(_: Path) -> list[Path]:
    return []


def project(tmp_path: Path, name: str = "cardak", **kw: object) -> Project:
    path = tmp_path / "repos" / name.replace(" ", "_")
    path.mkdir(parents=True, exist_ok=True)
    return Project(name, path, **kw)  # type: ignore[arg-type]


def config(*projects: Project, **kw: object) -> Config:
    return Config(projects=projects, **kw)  # type: ignore[arg-type]


def opaque_colors(png: Path) -> set[tuple[int, int, int]]:
    img = PilImage.open(png).convert("RGBA")
    return {(r, g, b) for r, g, b, a in img.get_flattened_data() if a == 255}  # type: ignore[misc]


# pick_logo


def test_logo_candidates_prefers_the_readme() -> None:
    readme = ["apps/b/podlog-logo-dark-theme.svg"]
    assert logo_candidates(readme, ["x/logo.png"])[:1] == ["apps/b/podlog-logo-dark-theme.svg"]


def test_logo_candidates_ranks_logo_mark_icon() -> None:
    tracked = ["a/icon.png", "a/cardak-mark-dark.svg", "public/next.svg", "a/b.txt"]
    assert logo_candidates([], tracked)[:1] == ["a/cardak-mark-dark.svg"]
    assert logo_candidates([], ["a/icon.png", "z/logo.ico"])[:1] == ["z/logo.ico"]


def test_logo_candidates_prefers_dark_then_short() -> None:
    tracked = ["p/flatpare_logo_blue.svg", "p/flatpare_logo_dark.svg", "p/flatpare_logo.svg"]
    assert logo_candidates([], tracked)[:1] == ["p/flatpare_logo_dark.svg"]
    assert logo_candidates([], ["deep/er/logo.png", "p/logo.png"])[:1] == ["p/logo.png"]


def test_logo_candidates_skips_screens_and_boilerplate() -> None:
    tracked = ["design/screens/01-quiet/logo.png", "public/vercel.svg", "tests/fixtures/logo.svg"]
    assert logo_candidates([], tracked) == []


def test_logo_candidates_skips_readme_entries_that_are_not_images() -> None:
    assert logo_candidates(["docs/demo.gif.txt", "a/logo.svg"], [])[:1] == ["a/logo.svg"]


# art files and the record


def test_art_files_are_slugs(tmp_path: Path, paths: Paths) -> None:
    p = project(tmp_path, "Cool App!")
    assert art_files([p], paths.art_dir) == {p.path: paths.art_dir / "cool-app.txt"}


def test_same_slug_gets_distinct_files(tmp_path: Path, paths: Paths) -> None:
    a, b = project(tmp_path, "my app"), project(tmp_path, "my-app")
    files = art_files([a, b], paths.art_dir)
    assert files[a.path] != files[b.path]
    assert {f.name[:6] for f in files.values()} == {"my-app"}


def test_empty_slug_is_project(tmp_path: Path, paths: Paths) -> None:
    p = project(tmp_path, "日本")
    assert art_files([p], paths.art_dir)[p.path].name == "project.txt"


def test_art_made_once_and_never_again(tmp_path: Path, paths: Paths) -> None:
    p = project(tmp_path)
    assert ensure_art(config(p), paths, no_logo) == []
    art = paths.art_dir / "cardak.txt"
    assert art.read_text() == name_art("cardak") + "\n"
    assert str(p.path.resolve()) in json.loads(paths.record.read_text())["generated"]
    art.unlink()
    assert ensure_art(config(p), paths, no_logo) == []
    assert not art.exists()


def test_existing_art_file_is_kept(tmp_path: Path, paths: Paths) -> None:
    p = project(tmp_path)
    paths.art_dir.mkdir()
    (paths.art_dir / "cardak.txt").write_text("mine\n")
    ensure_art(config(p), paths, no_logo)
    assert (paths.art_dir / "cardak.txt").read_text() == "mine\n"


def test_logo_becomes_the_art(tmp_path: Path, paths: Paths) -> None:
    p = project(tmp_path)
    ensure_art(config(p), paths, lambda _: [FIXTURES / "logos" / "mark.svg"])
    assert "@" in (paths.art_dir / "cardak.txt").read_text()


def test_next_logo_when_the_first_is_a_wordmark(tmp_path: Path, paths: Paths) -> None:
    from PIL import ImageDraw

    p = project(tmp_path)
    wide = tmp_path / "wordmark.png"
    img = PilImage.new("RGBA", (420, 70))
    ImageDraw.Draw(img).rectangle((10, 10, 409, 59), fill=(255, 0, 0, 255))
    img.save(wide)
    ensure_art(config(p), paths, lambda _: [wide, FIXTURES / "logos" / "mark.svg"])
    assert "@" in (paths.art_dir / "cardak.txt").read_text()


def test_logo_candidates_in_order() -> None:
    readme = ["site/cardak-wordmark-light.svg"]
    tracked = ["site/cardak-mark-dark.svg", "site/cardak-icon.svg", "x.txt"]
    assert logo_candidates(readme, tracked) == [
        "site/cardak-wordmark-light.svg",
        "site/cardak-mark-dark.svg",
        "site/cardak-icon.svg",
    ]


def test_broken_logo_falls_back_to_the_name(tmp_path: Path, paths: Paths) -> None:
    p = project(tmp_path)
    bad = tmp_path / "logo.png"
    bad.write_bytes(b"\x89PNG broken")
    ensure_art(config(p), paths, lambda _: [bad])
    assert (paths.art_dir / "cardak.txt").read_text() == name_art("cardak") + "\n"


def test_missing_project_dir_gets_name_art(tmp_path: Path, paths: Paths) -> None:
    p = Project("ghost", tmp_path / "gone")
    assert ensure_art(config(p), paths) == []
    assert (paths.art_dir / "ghost.txt").read_text() == name_art("ghost") + "\n"


def test_project_with_background_off_gets_no_art(tmp_path: Path, paths: Paths) -> None:
    p = project(tmp_path, background=False)
    ensure_art(config(p), paths, no_logo)
    assert not paths.art_dir.exists()
    ensure_art(config(project(tmp_path)), paths, no_logo)
    assert (paths.art_dir / "cardak.txt").exists()  # turned on later: made then


def test_globally_off_makes_no_art(tmp_path: Path, paths: Paths) -> None:
    ensure_art(config(project(tmp_path), background=BackgroundSettings(False)), paths, no_logo)
    assert not paths.art_dir.exists()


def test_write_failure_is_a_problem_and_not_recorded(tmp_path: Path, paths: Paths) -> None:
    paths.art_dir.parent.mkdir(exist_ok=True)
    paths.art_dir.write_text("a file, not a folder")
    problems = ensure_art(config(project(tmp_path)), paths, no_logo)
    assert problems and "cardak" in problems[0]
    assert not paths.record.exists()


def test_unreadable_record_is_a_problem_and_makes_nothing(tmp_path: Path, paths: Paths) -> None:
    paths.record.parent.mkdir(parents=True)
    paths.record.write_text("{not json")
    problems = ensure_art(config(project(tmp_path)), paths, no_logo)
    assert problems and "backgrounds.json" in problems[0]
    assert not paths.art_dir.exists()


def test_record_is_written_through_a_symlink(tmp_path: Path, paths: Paths) -> None:
    dotfiles = tmp_path / "dotfiles" / "backgrounds.json"
    dotfiles.parent.mkdir()
    dotfiles.write_text('{"version": 2, "generated": {}}\n')
    dotfiles.chmod(0o600)
    paths.record.parent.mkdir(parents=True)
    paths.record.symlink_to(dotfiles)
    p = project(tmp_path)
    assert ensure_art(config(p), paths, no_logo) == []
    assert paths.record.is_symlink()  # was replaced by a plain file (#130)
    assert str(p.path.resolve()) in json.loads(dotfiles.read_text())["generated"]
    assert dotfiles.stat().st_mode & 0o777 == 0o600


def test_making_art_holds_the_record_lock(tmp_path: Path, paths: Paths) -> None:
    lock = paths.record.with_name("backgrounds.lock")
    held: list[bool] = []

    def find(_: Path) -> list[Path]:
        with lock.open("a") as other:  # another process's attempt, while art is being made
            try:
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                held.append(True)
        return []

    assert ensure_art(config(project(tmp_path)), paths, find) == []
    assert held == [True]  # the read-modify-write is locked (#130)


def test_an_unopenable_lock_is_a_problem(tmp_path: Path, paths: Paths) -> None:
    paths.record.with_name("backgrounds.lock").mkdir(parents=True)  # a folder, not a file
    problems = ensure_art(config(project(tmp_path)), paths, no_logo)
    assert problems and "backgrounds.lock" in problems[0]
    assert not paths.art_dir.exists()


def test_a_failed_lock_is_a_problem(
    tmp_path: Path, paths: Paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_locks(fd: object, op: int) -> None:
        raise OSError(37, "No locks available")

    monkeypatch.setattr(fcntl, "flock", no_locks)
    problems = ensure_art(config(project(tmp_path)), paths, no_logo)
    assert problems and "couldn't lock" in problems[0]
    assert not paths.art_dir.exists()


# reading and rendering


def test_read_art(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("\n  x  \n\n")
    assert read_art(f) == "  x"
    assert read_art(tmp_path / "gone.txt") is None


def test_tabs_in_art_expand(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("a\tb\n")
    assert read_art(f) == "a       b"


def test_empty_art_means_no_background(tmp_path: Path, paths: Paths) -> None:
    p = project(tmp_path)
    paths.art_dir.mkdir()
    (paths.art_dir / "cardak.txt").write_text("  \n")
    found, _ = images(config(p), {}, paths, None)
    assert found == {}


def test_render_is_cached_and_sized(tmp_path: Path) -> None:
    first = render("ab\nabcd", "#ff0000", tmp_path, None)
    assert (first.cols, first.rows) == (4, 2)
    inode = first.file.stat().st_ino
    again = render("ab\nabcd", "#ff0000", tmp_path, None)
    assert again == first and again.file.stat().st_ino == inode  # not drawn again
    assert render("ab\nabcd", "#00ff00", tmp_path, None).file != first.file


def test_images_use_the_scheme_foreground(tmp_path: Path, paths: Paths) -> None:
    themed, plain = project(tmp_path, "a", theme="Light"), project(tmp_path, "b")
    found, problems = images(config(themed, plain), {"Light": LIGHT}, paths, None)
    assert problems == []
    assert opaque_colors(found[themed.path].file) == {(0x20, 0x20, 0x20)}
    assert opaque_colors(found[plain.path].file) == {(0xC0, 0xC0, 0xC0)}
    assert FALLBACK_COLOR == "#c0c0c0"


def test_images_fall_back_to_the_default_scheme(tmp_path: Path, paths: Paths) -> None:
    p = project(tmp_path)
    cfg = config(p, defaults={"theme": "Dark"})
    found, _ = images(cfg, {"Dark": DARK}, paths, None)
    assert opaque_colors(found[p.path].file) == {(0xE0, 0xE0, 0xE0)}


def _age(file: Path, seconds: float) -> None:
    then = time.time() - seconds
    os.utime(file, (then, then))


def test_images_prune_unused_pngs(tmp_path: Path, paths: Paths) -> None:
    paths.cache.mkdir()
    (paths.cache / "old.png").write_bytes(b"x")
    _age(paths.cache / "old.png", PRUNE_AFTER_S + 60)
    (paths.cache / "keep.txt").write_text("not ours")
    images(config(project(tmp_path)), {}, paths, None)
    assert not (paths.cache / "old.png").exists()
    assert (paths.cache / "keep.txt").exists()


def test_pruning_keeps_a_preview_still_shown(tmp_path: Path, paths: Paths) -> None:
    p = project(tmp_path)
    ensure_art(config(p), paths, no_logo)
    shown = preview_image(p, paths.art_dir / "cardak.txt", DARK, paths, None)
    assert shown is not None
    images(config(p), {}, paths, None)  # the deck writes the theme files mid-preview (#127)
    assert shown.file.exists()


def test_showing_an_old_preview_again_keeps_it(tmp_path: Path, paths: Paths) -> None:
    p = project(tmp_path)
    ensure_art(config(p), paths, no_logo)
    shown = preview_image(p, paths.art_dir / "cardak.txt", DARK, paths, None)
    assert shown is not None
    _age(shown.file, PRUNE_AFTER_S + 60)
    preview_image(p, paths.art_dir / "cardak.txt", DARK, paths, None)  # cached, shown again
    images(config(p), {}, paths, None)
    assert shown.file.exists()


def test_one_drawing_error_leaves_the_other_projects(
    tmp_path: Path, paths: Paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    good, bad = project(tmp_path, "good"), project(tmp_path, "bad")
    ensure_art(config(good, bad), paths, no_logo)
    (paths.art_dir / "bad.txt").write_text("broken\n")

    from cardak.core import backgrounds

    real_draw = backgrounds._draw  # noqa: SLF001

    def draw(lines: list[str], color: str, font: Path | None) -> bytes:
        if lines == ["broken"]:
            raise ValueError("bad glyph")  # as Pillow does on an odd art file
        return real_draw(lines, color, font)

    monkeypatch.setattr(backgrounds, "_draw", draw)
    found, problems = images(config(good, bad), {}, paths, None)  # was: ValueError (#125)
    assert set(found) == {good.path}
    assert problems == ["bad: couldn't draw its background: bad glyph"]
    assert preview_image(bad, paths.art_dir / "bad.txt", DARK, paths, None) is None


def test_images_globally_off(tmp_path: Path, paths: Paths) -> None:
    cfg = config(project(tmp_path), background=BackgroundSettings(False))
    assert images(cfg, {}, paths, None) == ({}, [])


def test_preview_image(tmp_path: Path, paths: Paths) -> None:
    p = project(tmp_path)
    ensure_art(config(p), paths, no_logo)
    shown = preview_image(p, paths.art_dir / "cardak.txt", DARK, paths, None)
    assert isinstance(shown, Image)
    assert opaque_colors(shown.file) == {(0xE0, 0xE0, 0xE0)}
    assert preview_image(None, None, DARK, paths, None) is None
    assert preview_image(p, tmp_path / "gone.txt", DARK, paths, None) is None


# find_logo on a real repository


def test_find_logos_in_a_git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    repo = tmp_path / "r"
    (repo / "assets").mkdir(parents=True)
    (repo / "assets" / "logo.svg").write_text("<svg/>")
    (repo / "untracked-logo.svg").write_text("<svg/>")
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "assets"], check=True)
    assert find_logos(repo) == [repo / "assets" / "logo.svg"]


def test_find_logos_from_the_readme_outside_git(tmp_path: Path) -> None:
    (tmp_path / "brand").mkdir()
    (tmp_path / "brand" / "w.png").write_bytes(b"x")
    (tmp_path / "README.md").write_text("![w](brand/w.png) ![x](../outside.png)")
    assert find_logos(tmp_path) == [tmp_path / "brand" / "w.png"]


def test_find_logos_never_leaves_the_project(tmp_path: Path) -> None:
    (tmp_path / "outside.png").write_bytes(b"x")
    project_dir = tmp_path / "p"
    project_dir.mkdir()
    (project_dir / "README.md").write_text("![x](../outside.png)")
    assert find_logos(project_dir) == []


def test_tools_for_the_picker(tmp_path: Path, paths: Paths) -> None:
    p = project(tmp_path)
    cfg = config(p)
    ensure_art(cfg, paths, no_logo)
    tools = Tools(paths, lambda: cfg, lambda: {"Dark": DARK}, lambda: None)
    assert tools.art_file(p.path) == paths.art_dir / "cardak.txt"
    assert tools.art_file(tmp_path / "unknown") is None
    shown = tools.preview_image(p.path, "Dark")
    assert shown is not None and opaque_colors(shown.file) == {(0xE0, 0xE0, 0xE0)}
    assert tools.preview_image(tmp_path / "unknown", "Dark") is None
    assert tools.off_reason() is None


def test_tools_name_no_art_file_before_it_is_made(tmp_path: Path, paths: Paths) -> None:
    p = project(tmp_path, background=False)
    tools = Tools(paths, lambda: config(p), lambda: {}, lambda: None)
    assert tools.art_file(p.path) is None  # switched off before its art was ever made (#126)


def test_tools_still_name_art_the_user_deleted(tmp_path: Path, paths: Paths) -> None:
    p = project(tmp_path)
    ensure_art(config(p), paths, no_logo)
    (paths.art_dir / "cardak.txt").unlink()
    tools = Tools(paths, lambda: config(p), lambda: {}, lambda: None)
    assert tools.art_file(p.path) == paths.art_dir / "cardak.txt"  # writing it brings it back


def test_tools_know_backgrounds_are_off(tmp_path: Path, paths: Paths) -> None:
    cfg = config(project(tmp_path), background=BackgroundSettings(enabled=False))
    off = Tools(paths, lambda: cfg, lambda: {}, lambda: None).off_reason()
    assert off == "background off for every project: [background] enabled = false"


def test_art_stamp_follows_edits(tmp_path: Path, paths: Paths) -> None:
    assert art_stamp(paths.art_dir) == ()
    paths.art_dir.mkdir()
    (paths.art_dir / "a.txt").write_text("x")
    first = art_stamp(paths.art_dir)
    (paths.art_dir / "a.txt").write_text("xy")
    assert art_stamp(paths.art_dir) != first


def test_a_same_named_newcomer_leaves_the_first_art_alone(tmp_path: Path, paths: Paths) -> None:
    first = project(tmp_path, "Foo")
    ensure_art(config(first), paths, no_logo)
    second = Project("foo", tmp_path / "elsewhere" / "foo")
    found, _ = images(config(first, second), {}, paths, None)
    assert (paths.art_dir / "foo.txt").exists()
    assert set(found) == {first.path, second.path}
    files = art_files([first, second], paths.art_dir, record_names(paths.record))
    assert files[first.path] == paths.art_dir / "foo.txt"
    assert files[second.path] != files[first.path]


def test_renaming_a_project_keeps_its_art(tmp_path: Path, paths: Paths) -> None:
    p = project(tmp_path)
    ensure_art(config(p), paths, no_logo)
    (paths.art_dir / "cardak.txt").write_text("mine\n")
    renamed = Project("deck", p.path)
    found, _ = images(config(renamed), {}, paths, None)
    tools = Tools(paths, lambda: config(renamed), lambda: {}, lambda: None)
    assert tools.art_file(p.path) == paths.art_dir / "cardak.txt"
    assert found[p.path].cols == 4  # "mine"


@pytest.mark.parametrize("generated", ['["/a"]', '{"/a": "../x.txt"}', '{"/a": 3}'])
def test_a_record_with_bad_entries_is_refused(tmp_path: Path, paths: Paths, generated: str) -> None:
    paths.record.parent.mkdir(parents=True)
    paths.record.write_text(f'{{"version": 2, "generated": {generated}}}')
    assert ensure_art(config(project(tmp_path)), paths, no_logo)
    assert record_names(paths.record) == {}
