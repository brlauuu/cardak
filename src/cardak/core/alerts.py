"""One desktop notification and one sound when a session starts waiting, or on a resource alert.

Both run detached, so `cardak hook` returns at once; a missing tool is skipped silently.
"""

import contextlib
import html
import random
import shutil
import subprocess
from collections.abc import Callable, Mapping
from importlib.resources import files
from pathlib import Path

from cardak.core.agents import KIND_LABELS, Ping
from cardak.core.config import NotifySettings

SOUND_EXTS = (".wav", ".oga", ".ogg")
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


def _user_sounds(settings: NotifySettings) -> dict[str, Path]:
    directory = settings.sounds_dir
    if directory is None or not directory.is_dir():
        return {}
    return {
        p.name: p for p in sorted(directory.iterdir()) if p.suffix in SOUND_EXTS and p.is_file()
    }


def sound_pool(settings: NotifySettings, bundled: Mapping[str, Path]) -> dict[str, Path]:
    return {**bundled, **_user_sounds(settings)}


def choose_sound(
    settings: NotifySettings, bundled: Mapping[str, Path], rng: random.Random
) -> Path | None:
    if settings.sound == "off":
        return None
    pool = sound_pool(settings, bundled)
    if settings.sound in pool:
        return pool[settings.sound]
    return rng.choice(list(pool.values())) if pool else None  # "random", or unknown


def check(settings: NotifySettings, bundled: Mapping[str, Path]) -> list[str]:
    """Problems worth a deck warning; the hook falls back to a random sound."""
    warnings = []
    if settings.sound not in ("random", "off") and settings.sound not in sound_pool(
        settings, bundled
    ):
        warnings.append(f'[notify] sound "{settings.sound}" not found; playing a random one')
    if settings.sounds_dir is not None and not settings.sounds_dir.is_dir():
        warnings.append(f"[notify] sounds_dir {settings.sounds_dir} is missing")
    return warnings


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
    _play(settings, spawn, rng, which)


def resource_alert(
    level: str,
    title: str,
    body: str,
    settings: NotifySettings,
    *,
    spawn: Spawn = spawn_detached,
    rng: random.Random | None = None,
    which: Which = shutil.which,
) -> None:
    if settings.desktop:
        spawn(resource_argv(level, title, body))
    _play(settings, spawn, rng, which)


def _play(settings: NotifySettings, spawn: Spawn, rng: random.Random | None, which: Which) -> None:
    sound = choose_sound(settings, bundled_sounds(), rng or random.Random())
    if sound is not None and (argv := player_argv(sound, which)):
        spawn(argv)
