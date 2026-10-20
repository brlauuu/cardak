import json
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from cardak.core.themes import Scheme
from cardak.wezterm.schemes import (
    PROBE,
    SchemesUnavailable,
    default_cache_dir,
    load_schemes,
    parse_schemes,
    read_cached_schemes,
)

ANSI = ["#000000", "#bb0000", "#00bb00", "#bbbb00", "#0000bb", "#bb00bb", "#00bbbb", "#bbbbbb"]
RAW = {
    "Nord": {
        "background": "#2e3440",
        "foreground": "#d8dee9",
        "cursor_bg": "#eceff4",
        "ansi": ANSI,
    },
    "Odd": {"background": "rgba(0,0,0,1)", "foreground": "#ffffff", "ansi": ANSI},
}


class FakeWezterm:
    """Answers `wezterm --version` and the probe; writes the probe output like real wezterm."""

    def __init__(
        self,
        output: object = RAW,
        version: str = "wezterm 20240203-110809-5046fc22",
        exc: Exception | None = None,
        write: bool = True,
    ) -> None:
        self.output, self.version, self.exc, self.write = output, version, exc, write
        self.calls: list[Sequence[str]] = []

    def __call__(self, cmd: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        self.calls.append(cmd)
        if self.exc:
            raise self.exc
        if list(cmd) == ["wezterm", "--version"]:
            return subprocess.CompletedProcess(cmd, 0, self.version + "\n", "")
        env = kwargs["env"]
        assert isinstance(env, dict)
        if self.write:
            text = self.output if isinstance(self.output, str) else json.dumps(self.output)
            Path(env["CARDAK_SCHEMES_OUT"]).write_text(text)
        return subprocess.CompletedProcess(cmd, 0, "", "")


def test_probe_lua() -> None:
    assert "get_builtin_schemes" in PROBE and "CARDAK_SCHEMES_OUT" in PROBE


def test_invalid_schemes_are_skipped() -> None:
    schemes = parse_schemes(RAW)
    assert list(schemes) == ["Nord"]
    assert schemes["Nord"] == Scheme("Nord", "#2e3440", "#d8dee9", "#eceff4", tuple(ANSI))
    with pytest.raises(SchemesUnavailable):
        parse_schemes(["not", "a", "dict"])


def test_load_probes_then_caches(tmp_path: Path) -> None:
    runner = FakeWezterm()
    assert list(load_schemes(tmp_path, runner)) == ["Nord"]
    probe_cmd = runner.calls[1]
    assert list(probe_cmd[:2]) == ["wezterm", "--config-file"]
    assert list(probe_cmd[3:]) == ["ls-fonts", "--list-system"]
    assert [p.name for p in tmp_path.iterdir()] == ["schemes-20240203-110809-5046fc22.json"]

    again = FakeWezterm(output={})  # would fail if it probed
    assert list(load_schemes(tmp_path, again)) == ["Nord"]
    assert again.calls == [["wezterm", "--version"]]


def test_read_cached_never_probes(tmp_path: Path) -> None:
    runner = FakeWezterm()
    assert read_cached_schemes(tmp_path, runner) is None
    assert runner.calls == [["wezterm", "--version"]]
    load_schemes(tmp_path, FakeWezterm())
    assert read_cached_schemes(tmp_path, FakeWezterm()) is not None
    assert read_cached_schemes(tmp_path, FakeWezterm(exc=FileNotFoundError())) is None


def test_cache_is_per_version(tmp_path: Path) -> None:
    load_schemes(tmp_path, FakeWezterm())
    newer = FakeWezterm(version="wezterm 20250101-000000-abcdef")
    load_schemes(tmp_path, newer)
    assert len(newer.calls) == 2  # version + probe


@pytest.mark.parametrize(
    ("runner", "reason"),
    [
        (FakeWezterm(exc=FileNotFoundError("wezterm")), "not installed"),
        (FakeWezterm(exc=subprocess.TimeoutExpired("wezterm", 20)), "timed out"),
        (FakeWezterm(write=False), "did not write"),
        (FakeWezterm(output="{nope"), "unexpected"),
        (FakeWezterm(output={}), "no schemes"),
    ],
)
def test_probe_failures(tmp_path: Path, runner: FakeWezterm, reason: str) -> None:
    with pytest.raises(SchemesUnavailable, match=reason):
        load_schemes(tmp_path, runner)


def test_default_cache_dir(tmp_path: Path) -> None:
    assert default_cache_dir({"XDG_CACHE_HOME": "/c"}) == Path("/c/cardak")
    assert default_cache_dir({"HOME": str(tmp_path)}) == tmp_path / ".cache/cardak"
