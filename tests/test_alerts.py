import random
import subprocess
from pathlib import Path

import pytest

from cardak.core.agents import Ping
from cardak.core.alerts import (
    alert,
    bundled_sounds,
    check,
    choose_sound,
    notification_argv,
    player_argv,
    sound_pool,
    spawn_detached,
)
from cardak.core.config import NotifySettings

PING = Ping("s1", 7, Path("/r/notes"), "question", "Want me to merge the two drafts?")
BUNDLED = {"chime": Path("/b/chime.wav"), "bell": Path("/b/bell.wav")}


def user_dir(tmp_path: Path) -> Path:
    d = tmp_path / "sounds"
    d.mkdir()
    for name in ("mine.wav", "other.ogg", "notes.txt"):
        (d / name).write_text("x")
    return d


def test_bundled_sounds_present() -> None:
    sounds = bundled_sounds()
    assert set(sounds) == {"chime", "pluck", "bell", "blip", "drop"}
    assert all(p.is_file() and p.suffix == ".wav" for p in sounds.values())


def test_sound_pool(tmp_path: Path) -> None:
    d = user_dir(tmp_path)
    pool = sound_pool(NotifySettings(sounds_dir=d), BUNDLED)
    assert set(pool) == {"chime", "bell", "mine.wav", "other.ogg"}
    assert set(sound_pool(NotifySettings(sounds_dir=tmp_path / "missing"), BUNDLED)) == {
        "chime",
        "bell",
    }


def test_choose_sound(tmp_path: Path) -> None:
    d = user_dir(tmp_path)
    rng = random.Random(1)
    assert choose_sound(NotifySettings(sound="off"), BUNDLED, rng) is None
    assert choose_sound(NotifySettings(sound="bell"), BUNDLED, rng) == BUNDLED["bell"]
    assert choose_sound(NotifySettings(sound="mine.wav", sounds_dir=d), BUNDLED, rng) == (
        d / "mine.wav"
    )
    picks = {choose_sound(NotifySettings(sounds_dir=d), BUNDLED, rng) for _ in range(60)}
    assert picks == {BUNDLED["chime"], BUNDLED["bell"], d / "mine.wav", d / "other.ogg"}
    assert choose_sound(NotifySettings(sound="nope"), BUNDLED, rng) in BUNDLED.values()
    assert choose_sound(NotifySettings(), {}, rng) is None


def test_check_warns_on_unknown_sound_and_missing_dir(tmp_path: Path) -> None:
    assert check(NotifySettings(), BUNDLED) == []
    assert check(NotifySettings(sound="off"), BUNDLED) == []
    warnings = check(NotifySettings(sound="nope", sounds_dir=tmp_path / "missing"), BUNDLED)
    assert len(warnings) == 2
    assert "nope" in warnings[0] and "missing" in warnings[1]


def test_notification_argv() -> None:
    assert notification_argv("notes", PING) == [
        "notify-send", "-a", "cardak", "notes · claude",
        "Your answer: Want me to merge the two drafts?",
    ]  # fmt: skip
    long = Ping("s1", 7, None, "finished", "word " * 60)
    body = notification_argv("x", long)[-1]
    assert len(body) == 120 and body.endswith("…")
    assert notification_argv("x", Ping("s1", 7, None, "finished", "a\nb"))[-1] == (
        "Turn finished: a b"
    )


def test_player_argv() -> None:
    sound = Path("/b/chime.wav")
    assert player_argv(sound, lambda name: f"/usr/bin/{name}") == ["/usr/bin/pw-play", str(sound)]
    only_pa = {"paplay": "/usr/bin/paplay"}
    assert player_argv(sound, only_pa.get) == ["/usr/bin/paplay", str(sound)]
    assert player_argv(sound, lambda _name: None) is None


def test_alert_spawns() -> None:
    spawned: list[list[str]] = []
    which = {"pw-play": "/usr/bin/pw-play"}.get
    alert("notes", PING, NotifySettings(desktop=False, sound="off"), spawn=spawned.append)
    assert spawned == []
    alert("notes", PING, NotifySettings(), spawn=spawned.append, rng=random.Random(0), which=which)
    assert [argv[0] for argv in spawned] == ["notify-send", "/usr/bin/pw-play"]


def test_spawn_detached_swallows_missing_binary() -> None:
    spawn_detached(["/nonexistent/cardak-test-binary"])


def test_spawn_detached_starts_new_session(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    class FakePopen:
        def __init__(self, argv: list[str], **kw: object) -> None:
            calls.append({"argv": argv, **kw})

    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    spawn_detached(["true"])
    assert calls[0]["start_new_session"] is True
    assert calls[0]["stdout"] is subprocess.DEVNULL
