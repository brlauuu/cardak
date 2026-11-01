import subprocess
from pathlib import Path

from cardak.collectors.fonts import monospace_font


def _runner(stdout: str, code: int = 0):
    def run(args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        assert args[:3] == ["fc-match", "-f", "%{file}"]
        return subprocess.CompletedProcess(args, code, stdout, "")

    return run


def test_monospace_font_path(tmp_path: Path) -> None:
    font = tmp_path / "Mono.ttf"
    font.write_bytes(b"")
    assert monospace_font(_runner(str(font))) == font


def test_monospace_font_that_does_not_exist(tmp_path: Path) -> None:
    assert monospace_font(_runner(str(tmp_path / "gone.ttf"))) is None


def test_monospace_font_failing_fc_match() -> None:
    assert monospace_font(_runner("", code=1)) is None


def test_monospace_font_missing_fc_match() -> None:
    def run(args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError(args[0])

    assert monospace_font(run) is None
