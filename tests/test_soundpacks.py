import json
import shutil
from pathlib import Path

from cardak.collectors.soundpacks import Clip, Packs, read_packs

FIXTURE = Path(__file__).parent / "fixtures" / "soundpacks" / "demo"
EXTS = (".wav", ".oga", ".ogg", ".mp3")


def _pack(root: Path, folder: str, categories: object, files: tuple[str, ...] = ()) -> Path:
    d = root / folder
    (d / "sounds").mkdir(parents=True)
    (d / "openpeon.json").write_text(json.dumps({"name": folder, "categories": categories}))
    for f in files:
        (d / f).write_text("x")
    return d


def _entry(*files: str) -> dict[str, list[dict[str, str]]]:
    return {"sounds": [{"file": f, "label": "x"} for f in files]}


def test_reads_clips(tmp_path: Path) -> None:
    d = tmp_path / "demo"
    shutil.copytree(FIXTURE, d)
    (d / "sounds").mkdir()
    for f in ("A.wav", "B.mp3"):
        (d / "sounds" / f).write_text("x")
    packs = read_packs(tmp_path, EXTS)
    assert packs == Packs(
        (
            Clip("demo/A", (d / "sounds/A.wav").resolve(), frozenset({"input.required"})),
            Clip("demo/B", (d / "sounds/B.mp3").resolve(), frozenset({"resource.limit"})),
        ),
        (),
    )


def test_clip_in_two_categories(tmp_path: Path) -> None:
    cats = {"input.required": _entry("sounds/A.wav"), "task.complete": _entry("sounds/A.wav")}
    _pack(tmp_path, "p", cats, ("sounds/A.wav",))
    (clip,) = read_packs(tmp_path, EXTS).clips
    assert clip.name == "p/A"
    assert clip.categories == frozenset({"input.required", "task.complete"})


def test_missing_and_unsupported_files_are_skipped(tmp_path: Path) -> None:
    cats = {"input.required": _entry("sounds/gone.wav", "sounds/notes.txt", "sounds/A.wav")}
    _pack(tmp_path, "p", cats, ("sounds/notes.txt", "sounds/A.wav"))
    packs = read_packs(tmp_path, EXTS)
    assert [c.name for c in packs.clips] == ["p/A"]
    assert packs.problems == ()


def test_paths_outside_the_pack_are_skipped(tmp_path: Path) -> None:
    root = tmp_path / "packs"
    root.mkdir()
    (root / "escape.wav").write_text("x")
    cats = {"input.required": _entry("../escape.wav", "/etc/hostname", "sounds/A.wav")}
    _pack(root, "p", cats, ("sounds/A.wav",))
    assert [c.name for c in read_packs(root, EXTS).clips] == ["p/A"]


def test_broken_packs_are_reported_not_fatal(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    (tmp_path / "README").write_text("my packs")
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "openpeon.json").write_text("{")
    _pack(tmp_path, "listy", [])
    cats = {"input.required": [{"file": 3}, "x", _entry("sounds/A.wav")["sounds"][0]]}
    _pack(
        tmp_path,
        "good",
        {"input.required": {"sounds": cats["input.required"]}, "x": 1},
        ("sounds/A.wav",),
    )
    packs = read_packs(tmp_path, EXTS)
    assert [c.name for c in packs.clips] == ["good/A"]
    assert len(packs.problems) == 2
    assert "bad" in packs.problems[0] and "listy" in packs.problems[1]


def test_uppercase_extension(tmp_path: Path) -> None:
    _pack(tmp_path, "p", {"input.required": _entry("sounds/C.WAV")}, ("sounds/C.WAV",))
    assert [c.name for c in read_packs(tmp_path, EXTS).clips] == ["p/C"]


def test_missing_root(tmp_path: Path) -> None:
    assert read_packs(tmp_path / "none", EXTS) == Packs((), ())
