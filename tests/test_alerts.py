import random
import subprocess
from pathlib import Path

import pytest

from cardak.core.agents import Ping
from cardak.core.alerts import (
    MAX_BODY,
    alert,
    bundled_sounds,
    check,
    choose_sound,
    notification_argv,
    player_argv,
    resource_alert,
    resource_argv,
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


def test_resource_argv_levels() -> None:
    assert resource_argv("critical", "cardak · memory critical", "x") == [
        "notify-send", "-a", "cardak", "-u", "critical", "cardak · memory critical", "x",
    ]  # fmt: skip
    assert resource_argv("warning", "t", "b")[4] == "normal"
    body = resource_argv("warning", "t", "y" * 1000)[-1]
    assert len(body) == 2 * MAX_BODY and body.endswith("…")


def test_resource_alert_spawns_notification_and_sound() -> None:
    spawned: list[list[str]] = []
    which = {"pw-play": "/usr/bin/pw-play"}.get
    resource_alert(
        "critical", "t", "b", NotifySettings(), spawn=spawned.append, rng=random.Random(0),
        which=which,
    )  # fmt: skip
    assert [argv[0] for argv in spawned] == ["notify-send", "/usr/bin/pw-play"]
    assert spawned[0][4] == "critical"


def test_resource_alert_follows_notify_settings() -> None:
    # same meaning as for waiting pings: desktop turns notify-send off, sound = "off" the sound
    spawned: list[list[str]] = []
    which = {"pw-play": "/usr/bin/pw-play"}.get
    resource_alert(
        "warning", "t", "b", NotifySettings(desktop=False, sound="off"), spawn=spawned.append,
        which=which,
    )  # fmt: skip
    assert spawned == []
    resource_alert(
        "warning", "t", "b", NotifySettings(desktop=False), spawn=spawned.append,
        rng=random.Random(0), which=which,
    )  # fmt: skip
    assert [argv[0] for argv in spawned] == ["/usr/bin/pw-play"]


def test_notification_body_is_escaped() -> None:
    ping = Ping("s1", 7, None, "permission", "Bash: cat <in >out && x")
    expected = "Approve a tool: Bash: cat &lt;in &gt;out &amp;&amp; x"
    assert notification_argv("x", ping)[-1] == expected


def test_resource_body_is_escaped() -> None:
    argv = resource_argv("warning", "t <b>", "pytest <3 & co")
    assert argv[-2:] == ["t <b>", "pytest &lt;3 &amp; co"]  # the summary is plain text


def test_cut_then_escaped() -> None:
    reason = "a" * 100 + " & " + "b" * 20  # the label is 15 chars: the & lands just before the cut
    body = notification_argv("x", Ping("s1", 7, None, "finished", reason))[-1]
    assert body.endswith("…") and "&am…" not in body and "&amp;" in body
