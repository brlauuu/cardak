"""Each project's ASCII art behind its windows (#9).

The art is made once per configured project, from its logo or else its name, into a text file
that is the user's from then on (`~/.config/cardak/logos/<slug>.txt`). A record in the state dir
says which projects have had theirs made, so a deleted file stays deleted. The text is drawn into
a PNG in the scheme's foreground, which `cardak.lua` shows in the window's corner.
"""

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from cardak.collectors import git
from cardak.collectors.logos import readme_images
from cardak.core.ascii_art import ArtError, load_image, logo_art, name_art
from cardak.core.config import Config, Project, cache_dir, config_dir, real_path, state_dir
from cardak.core.themes import Scheme

if TYPE_CHECKING:
    from PIL.ImageFont import FreeTypeFont, ImageFont

FALLBACK_COLOR = "#c0c0c0"  # no scheme known: light, for a dark terminal
FONT_PX = 28
IMAGE_EXTS = {".svg", ".png", ".jpg", ".jpeg", ".webp", ".ico"}
RANKS = (("logo",), ("mark", "wordmark"), ("icon",))
SKIP_NAMES = {"next.svg", "vercel.svg", "file.svg", "globe.svg", "window.svg", "favicon.ico"}
SKIP_PARTS = {"screens", "screenshots", "design", "node_modules", "test", "tests", "fixtures"}
RECORD_VERSION = 1
MAX_TRIES = 8  # logo candidates tried before the name is used


@dataclass(frozen=True)
class Image:
    """A rendered PNG and its size in terminal cells."""

    file: Path
    cols: int
    rows: int


@dataclass(frozen=True)
class Paths:
    art_dir: Path  # the user's text files
    record: Path  # which projects have had their art made
    cache: Path  # rendered PNGs

    @classmethod
    def default(cls, env: Mapping[str, str] | None = None) -> "Paths":
        return cls(
            config_dir(env) / "logos",
            state_dir(env) / "backgrounds.json",
            cache_dir(env) / "backgrounds",
        )


def _usable(path: str) -> bool:
    p = PurePosixPath(path)
    return (
        p.suffix.lower() in IMAGE_EXTS
        and p.name.lower() not in SKIP_NAMES
        and not SKIP_PARTS & {part.lower() for part in p.parts[:-1]}
    )


def logo_candidates(readme: Sequence[str], tracked: Sequence[str]) -> list[str]:
    """Logo files, best first: the README's images, then tracked images named logo, then mark,
    then icon; within each, a `dark` variant first (terminals are mostly dark), then the
    shortest path."""
    found = [p for p in readme if _usable(p)]
    for words in RANKS:
        named = [
            p
            for p in tracked
            if _usable(p) and any(w in PurePosixPath(p).name.lower() for w in words)
        ]
        named.sort(key=lambda p: ("dark" not in PurePosixPath(p).name.lower(), len(p)))
        found += named
    return list(dict.fromkeys(found))


def find_logos(project: Path) -> list[Path]:
    """The project's logo files, best first, read-only: the README's images, then git's
    tracked files. Only files inside the project."""
    root = real_path(project)

    def inside(rel: str) -> Path | None:
        path = real_path(project / rel)
        return path if path.is_relative_to(root) and path.is_file() else None

    readme = [rel for rel in readme_images(project) if inside(rel)]
    tracked = git.tracked_files(project) or ()  # relative to the project, even in a subfolder
    return [path for rel in logo_candidates(readme, tracked) if (path := inside(rel))]


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9._-]+", "-", name.lower()).strip("-.") or "project"


def _first_per_path(projects: Iterable[Project]) -> list[Project]:
    kept: dict[Path, Project] = {}
    for p in projects:
        kept.setdefault(real_path(p.path), p)
    return list(kept.values())


def art_files(projects: Sequence[Project], art_dir: Path) -> dict[Path, Path]:
    """Each project's text file, keyed by its path; a slug two projects share gets a suffix."""
    slugs = {p.path: _slug(p.name) for p in projects}
    counts: dict[str, int] = {}
    for slug in slugs.values():
        counts[slug] = counts.get(slug, 0) + 1
    files: dict[Path, Path] = {}
    for path, slug in slugs.items():
        if counts[slug] > 1:
            slug += "-" + hashlib.sha1(str(real_path(path)).encode()).hexdigest()[:6]
        files[path] = art_dir / f"{slug}.txt"
    return files


def read_art(file: Path) -> str | None:
    """The art with tabs expanded and trailing whitespace gone; None when there is none."""
    try:
        text = file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    lines = [line.expandtabs().rstrip() for line in text.splitlines()]
    while lines and not lines[0]:
        lines.pop(0)
    art = "\n".join(lines).rstrip()
    return art or None


def _write_atomic(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _read_record(record: Path) -> set[str]:
    """The projects whose art was made. Raises ValueError when the file can't be trusted."""
    try:
        data = json.loads(record.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return set()
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError(str(e)) from e
    generated = data.get("generated") if isinstance(data, dict) else None
    if not isinstance(generated, list) or not all(isinstance(g, str) for g in generated):
        raise ValueError("not a list of projects")
    return set(generated)


def _logo_art(logos: Sequence[Path]) -> str | None:
    """The first candidate that makes art; a wordmark or a broken file is skipped."""
    for logo in logos[:MAX_TRIES]:
        try:
            return logo_art(load_image(logo))
        except ArtError:
            continue
    return None


def _make(project: Project, file: Path, find: Callable[[Path], Sequence[Path]]) -> None:
    """Write the project's art unless the file exists. Raises OSError."""
    art = _logo_art(find(project.path))
    file.parent.mkdir(parents=True, exist_ok=True)
    try:
        with file.open("x", encoding="utf-8") as f:
            f.write((art or name_art(project.name)) + "\n")
    except FileExistsError:
        pass  # the user's own art, made before cardak got to it


def _wanted(config: Config) -> list[Project]:
    if not config.background.enabled:
        return []
    return [p for p in _first_per_path(config.projects) if p.background]


def ensure_art(
    config: Config, paths: Paths, find: Callable[[Path], Sequence[Path]] = find_logos
) -> list[str]:
    """Make the art of every project that never had it; returns problems for the deck."""
    wanted = _wanted(config)
    if not wanted:
        return []
    try:
        done = _read_record(paths.record)
    except ValueError as e:
        return [f"{paths.record.name}: {e}; no project background is made until it's fixed"]
    files = art_files(_first_per_path(config.projects), paths.art_dir)
    problems: list[str] = []
    made = False
    for p in wanted:
        key = str(real_path(p.path))
        if key in done:
            continue
        try:
            _make(p, files[p.path], find)
        except OSError as e:
            problems.append(f"{p.name}: couldn't write its background art: {e}")
            continue
        done.add(key)
        made = True
    if made:
        data = {"version": RECORD_VERSION, "generated": sorted(done)}
        try:
            _write_atomic(paths.record, (json.dumps(data, indent=1) + "\n").encode())
        except OSError as e:
            problems.append(f"couldn't write {paths.record}: {e}")
    return problems


def _font(font: Path | None) -> "FreeTypeFont | ImageFont":
    from PIL.ImageFont import load_default, truetype

    if font is not None:
        try:
            return truetype(str(font), FONT_PX)
        except OSError:
            pass
    return load_default(FONT_PX)


def render(text: str, color: str, cache: Path, font: Path | None) -> Image:
    """The text drawn in color on a transparent PNG, cached by what it was drawn from."""
    lines = text.splitlines() or [""]
    cols, rows = max(1, max(map(len, lines))), len(lines)
    key = hashlib.sha1("\0".join((text, color, str(font), str(FONT_PX))).encode()).hexdigest()
    target = cache / f"{key[:16]}.png"
    if not target.exists():
        from io import BytesIO

        from PIL import Image as PilImage
        from PIL import ImageDraw

        face = _font(font)
        cell_w = max(1, round(face.getlength("M")))
        cell_h = max(round(face.getbbox("M|gjy")[3]), FONT_PX) + 2
        img = PilImage.new("RGBA", (cell_w * cols, cell_h * rows))
        draw = ImageDraw.Draw(img)
        for i, line in enumerate(lines):
            draw.text((0, i * cell_h), line, font=face, fill=color)
        out = BytesIO()
        img.save(out, "PNG")
        _write_atomic(target, out.getvalue())
    return Image(target, cols, rows)


def _color(project: Project, config: Config, schemes: Mapping[str, Scheme]) -> str:
    default = config.defaults.get("theme")
    for name in (project.theme, default if isinstance(default, str) else None):
        if name and (scheme := schemes.get(name)):
            return scheme.foreground
    return FALLBACK_COLOR


def images(
    config: Config, schemes: Mapping[str, Scheme], paths: Paths, font: Path | None
) -> tuple[dict[Path, Image], list[str]]:
    """Make missing art, then each project's image, keyed by its path; old PNGs are removed."""
    wanted = _wanted(config)
    if not wanted:
        return {}, []
    problems = ensure_art(config, paths)
    files = art_files(_first_per_path(config.projects), paths.art_dir)
    found: dict[Path, Image] = {}
    for p in wanted:
        if (art := read_art(files[p.path])) is None:
            continue
        try:
            found[p.path] = render(art, _color(p, config, schemes), paths.cache, font)
        except OSError as e:
            problems.append(f"{p.name}: couldn't draw its background: {e}")
    keep = {image.file.name for image in found.values()}
    for old in paths.cache.glob("*.png"):
        if old.name not in keep:
            old.unlink(missing_ok=True)
    return found, problems


def preview_image(
    project: Project | None,
    art_file: Path | None,
    scheme: Scheme | None,
    paths: Paths,
    font: Path | None,
) -> Image | None:
    """A configured project's art in a previewed scheme's foreground; None when there's none."""
    if project is None or art_file is None or (art := read_art(art_file)) is None:
        return None
    try:
        return render(art, scheme.foreground if scheme else FALLBACK_COLOR, paths.cache, font)
    except OSError:
        return None


class Tools:
    """The theme picker's view of the backgrounds: a project's art file and previews of it."""

    def __init__(
        self,
        paths: Paths,
        config: Callable[[], Config],
        schemes: Callable[[], Mapping[str, Scheme]],
        font: Callable[[], Path | None],
    ) -> None:
        self._paths = paths
        self._config = config
        self._schemes = schemes
        self._font = font

    def _project(self, project_path: Path) -> tuple[Project, Path] | None:
        projects = _first_per_path(self._config().projects)
        project = next((p for p in projects if p.path == project_path), None)
        if project is None:
            return None
        return project, art_files(projects, self._paths.art_dir)[project.path]

    def art_file(self, project_path: Path) -> Path | None:
        found = self._project(project_path)
        return found[1] if found else None

    def preview_image(self, project_path: Path, scheme: str) -> Image | None:
        found = self._project(project_path)
        if found is None:
            return None
        project, file = found
        return preview_image(project, file, self._schemes().get(scheme), self._paths, self._font())
