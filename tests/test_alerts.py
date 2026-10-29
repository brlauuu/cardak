import random
import subprocess
from pathlib import Path

import pytest

from cardak.collectors.soundpacks import Clip, Packs
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
    spawn_detached,
    theme_pool,
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


def _packs(*clips: tuple[str, str]) -> Packs:
    return Packs(tuple(Clip(n, Path(f"/p/{n}.wav"), frozenset({c})) for n, c in clips), ())


PACKS = _packs(
    ("demo/A", "input.required"), ("demo/B", "resource.limit"), ("demo/C", "task.complete")
)
NO_PACKS = Packs((), ())


def clip(packs: Packs, name: str) -> Path:
    return next(c.path for c in packs.clips if c.name == name)


def test_waiting_pool_with_packs(tmp_path: Path) -> None:
    d = user_dir(tmp_path)
    pool = theme_pool("waiting", NotifySettings(sounds_dir=d), BUNDLED, PACKS)
    assert set(pool) == {clip(PACKS, "demo/A"), d / "mine.wav", d / "other.ogg"}


def test_waiting_pool_without_packs(tmp_path: Path) -> None:
    d = user_dir(tmp_path)
    pool = theme_pool("waiting", NotifySettings(sounds_dir=d), BUNDLED, NO_PACKS)
    assert set(pool) == {*BUNDLED.values(), d / "mine.wav", d / "other.ogg"}
    missing = NotifySettings(sounds_dir=tmp_path / "missing")
    assert set(theme_pool("waiting", missing, BUNDLED, NO_PACKS)) == set(BUNDLED.values())


def test_memory_pool() -> None:
    assert theme_pool("memory", NotifySettings(), BUNDLED, PACKS) == [clip(PACKS, "demo/B")]
    assert theme_pool("memory", NotifySettings(), BUNDLED, NO_PACKS) == [BUNDLED["bell"]]


def test_other_pool_is_chime() -> None:
    assert theme_pool("other", NotifySettings(), BUNDLED, PACKS) == [BUNDLED["chime"]]


def test_choose_named_pack_clip() -> None:
    rng = random.Random(1)
    named = NotifySettings(waiting_sound="demo/C")
    assert choose_sound("waiting", named, BUNDLED, PACKS, rng) == clip(PACKS, "demo/C")
    assert (
        choose_sound("waiting", NotifySettings(sound="bell"), BUNDLED, PACKS, rng)
        == (BUNDLED["bell"])
    )


def test_choose_named_user_file(tmp_path: Path) -> None:
    d = user_dir(tmp_path)
    s = NotifySettings(other_sound="mine.wav", sounds_dir=d)
    assert choose_sound("other", s, BUNDLED, NO_PACKS, random.Random(1)) == d / "mine.wav"


def test_choose_off_and_unknown() -> None:
    rng = random.Random(1)
    assert choose_sound("memory", NotifySettings(memory_sound="off"), BUNDLED, PACKS, rng) is None
    assert choose_sound("waiting", NotifySettings(sound="off"), BUNDLED, PACKS, rng) is None
    unknown = NotifySettings(memory_sound="nope")
    assert choose_sound("memory", unknown, BUNDLED, PACKS, rng) == clip(PACKS, "demo/B")
    assert choose_sound("waiting", NotifySettings(), {}, NO_PACKS, rng) is None


def test_random_waiting_spreads_over_the_pool(tmp_path: Path) -> None:
    d = user_dir(tmp_path)
    rng = random.Random(1)
    s = NotifySettings(sounds_dir=d)
    picks = {choose_sound("waiting", s, BUNDLED, PACKS, rng) for _ in range(60)}
    assert picks == {clip(PACKS, "demo/A"), d / "mine.wav", d / "other.ogg"}


def test_mp3_in_sounds_dir(tmp_path: Path) -> None:
    d = user_dir(tmp_path)
    (d / "x.mp3").write_text("x")
    assert d / "x.mp3" in theme_pool("waiting", NotifySettings(sounds_dir=d), BUNDLED, NO_PACKS)


def test_check_warns_on_unknown_sound_and_missing_dir(tmp_path: Path) -> None:
    assert check(NotifySettings(), BUNDLED, PACKS) == []
    assert check(NotifySettings(sound="off", other_sound="demo/C"), BUNDLED, PACKS) == []
    warnings = check(NotifySettings(sound="nope", sounds_dir=tmp_path / "missing"), BUNDLED, PACKS)
    assert warnings == [
        '[notify] sound "nope" not found; playing a random one',
        f"[notify] sounds_dir {tmp_path / 'missing'} is missing",
    ]


def test_check_names_the_theme_key() -> None:
    warnings = check(NotifySettings(memory_sound="nope"), BUNDLED, PACKS)
    assert warnings == ['[notify] memory_sound "nope" not found; playing a random one']
    broken = Packs((), ("bad: openpeon.json unreadable (x)",))
    assert check(NotifySettings(), BUNDLED, broken) == [
        "sound pack bad: openpeon.json unreadable (x)"
    ]


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


def test_resource_alert_plays_its_theme(packs_dir: Path) -> None:
    pack = packs_dir / "demo"
    (pack / "sounds").mkdir(parents=True)
    (pack / "sounds" / "B.wav").write_text("x")
    (pack / "openpeon.json").write_text(
        '{"categories": {"resource.limit": {"sounds": [{"file": "sounds/B.wav"}]}}}'
    )
    spawned: list[list[str]] = []
    which = {"pw-play": "/usr/bin/pw-play"}.get
    for theme in ("other", "memory"):
        resource_alert(
            "warning", "t", "b", NotifySettings(desktop=False), theme=theme,
            spawn=spawned.append, rng=random.Random(0), which=which,
        )  # fmt: skip
    assert spawned[0][1] == str(bundled_sounds()["chime"])
    assert spawned[1][1] == str((pack / "sounds" / "B.wav").resolve())


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
