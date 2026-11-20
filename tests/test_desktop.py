import subprocess
from pathlib import Path

from cardak.collectors.desktop import open_file


class Proc:
    def __init__(self, code: int | None) -> None:
        self.code = code
        self.killed = False

    def wait(self, timeout: float) -> int:
        if self.code is None:
            raise subprocess.TimeoutExpired("xdg-open", timeout)
        return self.code

    def kill(self) -> None:
        self.killed = True


class Run:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(self, argv: list[str], **kw: object) -> Proc:
        self.calls.append((argv, kw))
        if isinstance(self.result, BaseException):
            raise self.result
        assert isinstance(self.result, Proc)
        return self.result


def test_open_file_runs_xdg_open_detached(tmp_path: Path) -> None:
    f = tmp_path / "web.txt"
    f.write_text("art")
    run = Run(Proc(0))
    assert open_file(f, run=run) is None  # type: ignore[arg-type]
    argv, kw = run.calls[0]
    assert argv == ["xdg-open", str(f)]
    assert kw["start_new_session"] is True
    assert kw["stdin"] == kw["stdout"] == kw["stderr"] == subprocess.DEVNULL


def test_open_file_says_why_it_failed(tmp_path: Path) -> None:
    f = tmp_path / "web.txt"
    f.write_text("art")
    assert open_file(f, run=Run(Proc(4))) == "xdg-open failed (exit 4)"  # type: ignore[arg-type]
    missing = Run(FileNotFoundError(2, "No such file", "xdg-open"))
    assert open_file(f, run=missing) == "xdg-open isn't installed"  # type: ignore[arg-type]
    denied = Run(PermissionError(13, "Permission denied"))
    assert "Permission denied" in (open_file(f, run=denied) or "")  # type: ignore[arg-type]


def test_an_opener_still_running_counts_as_opened_and_is_left_alone(tmp_path: Path) -> None:
    f = tmp_path / "web.txt"
    f.write_text("art")
    proc = Proc(None)
    assert open_file(f, run=Run(proc)) is None  # type: ignore[arg-type]
    assert not proc.killed


def test_a_file_that_is_gone_is_not_handed_to_xdg_open(tmp_path: Path) -> None:
    run = Run(Proc(0))
    assert open_file(tmp_path / "gone.txt", run=run) == "no such file"  # type: ignore[arg-type]
    assert run.calls == []
