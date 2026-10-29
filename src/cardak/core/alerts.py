"""One desktop notification and one sound when a session starts waiting, or on a resource alert.

Both run detached, so `cardak hook` returns at once; a missing tool is skipped silently. The sound
depends on the theme (#91): waiting, memory or other, each set to random, off or one name.
Random draws from the user's sound packs when they have clips for it, else from cardak's own.
"""

import contextlib
import html
import random
import shutil
import subprocess
from collections.abc import Callable, Mapping
from importlib.resources import files
from pathlib import Path

from cardak.collectors.soundpacks import Packs, read_packs
from cardak.core.agents import KIND_LABELS, Ping
from cardak.core.config import THEMES, NotifySettings, Theme

SOUND_EXTS = (".wav", ".oga", ".ogg", ".mp3")
CATEGORY: Mapping[Theme, str] = {"waiting": "input.required", "memory": "resource.limit"}
FALLBACK: Mapping[Theme, str] = {"memory": "bell", "other": "chime"}  # bundled stems
PLAYERS = ("pw-play", "paplay")
MAX_BODY = 120

Spawn = Callable[[list[str]], None]
Which = Callable[[str], str | None]


def bundled_sounds() -> dict[str, Path]:
    pack = files("cardak.sounds")
    return {
        Path(entry.name).stem: Path(str(entry))
        for entry in pack.iterdir()
        if entry.name.endswith(".wav")
    }


def pack_root() -> Path:
    """Where the user copies their packs; git-ignored and left out of builds."""
    return Path(str(files("cardak.sounds"))) / "packs"


def _user_sounds(settings: NotifySettings) -> dict[str, Path]:
    directory = settings.sounds_dir
    if directory is None or not directory.is_dir():
        return {}
    return {
        p.name: p
        for p in sorted(directory.iterdir())
        if p.suffix.lower() in SOUND_EXTS and p.is_file()
    }


def named_sounds(
    settings: NotifySettings, bundled: Mapping[str, Path], packs: Packs
) -> dict[str, Path]:
    return {**bundled, **_user_sounds(settings), **{c.name: c.path for c in packs.clips}}


def theme_pool(
    theme: Theme, settings: NotifySettings, bundled: Mapping[str, Path], packs: Packs
) -> list[Path]:
    """What random picks from: the packs' clips for the theme, else cardak's fallback. Waiting
    keeps the user's sounds_dir too, and today's whole pool when no pack has a clip for it."""
    category = CATEGORY.get(theme)
    clips = [c.path for c in packs.clips if category in c.categories]
    if theme == "waiting":
        own = list(_user_sounds(settings).values())
        return clips + own if clips else [*bundled.values(), *own]
    if clips:
        return clips
    return [bundled[FALLBACK[theme]]] if FALLBACK[theme] in bundled else []


def choose_sound(
    theme: Theme,
    settings: NotifySettings,
    bundled: Mapping[str, Path],
    packs: Packs,
    rng: random.Random,
) -> Path | None:
    wanted = settings.theme_sound(theme)
    if wanted == "off":
        return None
    if wanted != "random" and (named := named_sounds(settings, bundled, packs).get(wanted)):
        return named
    pool = theme_pool(theme, settings, bundled, packs)
    return rng.choice(pool) if pool else None  # "random", or unknown


def check(settings: NotifySettings, bundled: Mapping[str, Path], packs: Packs) -> list[str]:
    """Problems worth a deck warning; the hook and the daemon fall back to a random sound."""
    names = named_sounds(settings, bundled, packs)
    warnings = [
        f'[notify] {settings.theme_key(t)} "{wanted}" not found; playing a random one'
        for t in THEMES
        if (wanted := settings.theme_sound(t)) not in ("random", "off") and wanted not in names
    ]
    if settings.sounds_dir is not None and not settings.sounds_dir.is_dir():
        warnings.append(f"[notify] sounds_dir {settings.sounds_dir} is missing")
    return warnings + [f"sound pack {p}" for p in packs.problems]


def notification_argv(project: str, ping: Ping) -> list[str]:
    body = _cut(" ".join(f"{KIND_LABELS[ping.kind]}: {ping.reason}".split()), MAX_BODY)
    return ["notify-send", "-a", "cardak", f"{project} · claude", _markup_safe(body)]


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _markup_safe(body: str) -> str:
    """The body is rendered as (a subset of) HTML by KDE and others; the summary is plain text.
    Escaped after cutting, so a cut never splits an entity."""
    return html.escape(body, quote=False)


def resource_argv(level: str, title: str, body: str) -> list[str]:
    urgency = "critical" if level == "critical" else "normal"  # critical stays until dismissed
    body = _markup_safe(_cut(body, 2 * MAX_BODY))
    return ["notify-send", "-a", "cardak", "-u", urgency, title, body]


def player_argv(sound: Path, which: Which = shutil.which) -> list[str] | None:
    for player in PLAYERS:
        if found := which(player):
            return [found, str(sound)]
    return None


def spawn_detached(argv: list[str]) -> None:
    with contextlib.suppress(OSError):
        subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )


def alert(
    project: str,
    ping: Ping,
    settings: NotifySettings,
    *,
    spawn: Spawn = spawn_detached,
    rng: random.Random | None = None,
    which: Which = shutil.which,
) -> None:
    if settings.desktop:
        spawn(notification_argv(project, ping))
    _play("waiting", settings, spawn, rng, which)


def resource_alert(
    level: str,
    title: str,
    body: str,
    settings: NotifySettings,
    *,
    theme: Theme = "other",
    spawn: Spawn = spawn_detached,
    rng: random.Random | None = None,
    which: Which = shutil.which,
) -> None:
    if settings.desktop:
        spawn(resource_argv(level, title, body))
    _play(theme, settings, spawn, rng, which)


def _play(
    theme: Theme, settings: NotifySettings, spawn: Spawn, rng: random.Random | None, which: Which
) -> None:
    packs = read_packs(pack_root(), SOUND_EXTS)
    sound = choose_sound(theme, settings, bundled_sounds(), packs, rng or random.Random())
    if sound is not None and (argv := player_argv(sound, which)):
        spawn(argv)
