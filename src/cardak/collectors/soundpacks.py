"""Sound packs in the openpeon format, put by the user under one folder (#91).

Each pack is a folder with an `openpeon.json` listing its clips by category (`input.required`,
`resource.limit`, …), each as a `file` relative to the pack. Not cardak's format, so anything
unexpected is skipped: a clip that's missing, of another type or outside its pack, silently; a
pack whose manifest can't be read, with a problem line.
"""

import json
from dataclasses import dataclass
from pathlib import Path

MANIFEST = "openpeon.json"


@dataclass(frozen=True)
class Clip:
    name: str  # "<folder>/<file stem>"
    path: Path
    categories: frozenset[str]


@dataclass(frozen=True)
class Packs:
    clips: tuple[Clip, ...]
    problems: tuple[str, ...]


def read_packs(root: Path, exts: tuple[str, ...]) -> Packs:
    clips: list[Clip] = []
    problems: list[str] = []
    try:
        folders = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return Packs((), ())
    for folder in folders:
        if not (folder / MANIFEST).is_file():
            continue
        try:
            categories = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))["categories"]
            if not isinstance(categories, dict):
                raise ValueError("categories is not a table")
        except (OSError, ValueError, KeyError, TypeError) as e:
            problems.append(f"{folder.name}: {MANIFEST} unreadable ({e})")
            continue
        clips.extend(_clips(folder, categories, exts))
    return Packs(tuple(clips), tuple(problems))


def _clips(folder: Path, categories: dict[object, object], exts: tuple[str, ...]) -> list[Clip]:
    found: dict[Path, set[str]] = {}
    base = folder.resolve()
    for category, entry in categories.items():
        sounds = entry.get("sounds") if isinstance(entry, dict) else None
        for sound in sounds if isinstance(sounds, list) else ():
            file = sound.get("file") if isinstance(sound, dict) else None
            if not isinstance(file, str) or not isinstance(category, str):
                continue
            path = (folder / file).resolve()
            if path.is_relative_to(base) and path.suffix.lower() in exts and path.is_file():
                found.setdefault(path, set()).add(category)
    return [Clip(f"{folder.name}/{p.stem}", p, frozenset(c)) for p, c in found.items()]
