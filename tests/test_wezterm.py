import os
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from cardak.collectors.wezterm import (
    Pane,
    WeztermUnavailable,
    activate_pane,
    find_socket,
    list_panes,
    parse_cwd,
    parse_panes,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_basic() -> None:
    panes = parse_panes((FIXTURES / "wezterm_list_basic.json").read_text())
    assert [p.pane_id for p in panes] == [3, 0, 4, 2]
    assert panes[1] == Pane(
        pane_id=0,
        window_id=0,
        tab_id=0,
        workspace="default",
        title="~/repos/cardak",
        cwd=Path("/home/user/repos/cardak"),
        tty="/dev/pts/5",
        is_active=True,
    )


def test_parse_edge_cases() -> None:
    empty_cwd, hosted, bare = parse_panes((FIXTURES / "wezterm_list_edge.json").read_text())
    assert empty_cwd.cwd is None
    assert hosted.cwd == Path("/home/user/repos/my proj")
    assert hosted.workspace == "work"
    assert bare.cwd is None and bare.tty is None and bare.is_active is False


def test_parse_cwd_rejects_other_schemes() -> None:
    assert parse_cwd("http://example.com/x") is None
    assert parse_cwd(None) is None
    assert parse_cwd("file://") is None


def _dead_pid() -> int:
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


def _runtime(tmp_path: Path) -> Path:
    d = tmp_path / "wezterm"
    d.mkdir()
    return d


def test_explicit_socket_wins(tmp_path: Path) -> None:
    explicit = tmp_path / "explicit"
    explicit.touch()
    (_runtime(tmp_path) / f"gui-sock-{os.getpid()}").touch()
    env = {"WEZTERM_UNIX_SOCKET": str(explicit), "XDG_RUNTIME_DIR": str(tmp_path)}
    assert find_socket(env) == explicit


def test_missing_explicit_socket_falls_through_to_gui(tmp_path: Path) -> None:
    gui = _runtime(tmp_path) / f"gui-sock-{os.getpid()}"
    gui.touch()
    env = {"WEZTERM_UNIX_SOCKET": str(tmp_path / "gone"), "XDG_RUNTIME_DIR": str(tmp_path)}
    assert find_socket(env) == gui


def test_newest_live_gui_socket_wins(tmp_path: Path) -> None:
    d = _runtime(tmp_path)
    old, new = d / f"gui-sock-{os.getppid()}", d / f"gui-sock-{os.getpid()}"
    old.touch()
    new.touch()
    os.utime(old, (1_000, 1_000))
    os.utime(new, (2_000, 2_000))
    assert find_socket({"XDG_RUNTIME_DIR": str(tmp_path)}) == new


def test_dead_gui_socket_skipped_for_mux_socket(tmp_path: Path) -> None:
    d = _runtime(tmp_path)
    (d / f"gui-sock-{_dead_pid()}").touch()
    (d / "gui-sock-notapid").touch()
    (d / "sock").touch()
    assert find_socket({"XDG_RUNTIME_DIR": str(tmp_path)}) == d / "sock"


def test_no_socket(tmp_path: Path) -> None:
    _runtime(tmp_path)
    assert find_socket({"XDG_RUNTIME_DIR": str(tmp_path)}) is None
    assert find_socket({}) is None


class FakeRunner:
    def __init__(
        self,
        stdout: str = "[]",
        stderr: str = "",
        returncode: int = 0,
        exc: Exception | None = None,
    ) -> None:
        self.stdout, self.stderr, self.returncode, self.exc = stdout, stderr, returncode, exc
        self.calls: list[tuple[Sequence[str], dict[str, object]]] = []

    def __call__(self, cmd: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        self.calls.append((cmd, kwargs))
        if self.exc:
            raise self.exc
        return subprocess.CompletedProcess(cmd, self.returncode, self.stdout, self.stderr)


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    sock = tmp_path / "gui-sock"
    sock.touch()
    return {"WEZTERM_UNIX_SOCKET": str(sock), "PATH": "/usr/bin"}


def test_list_panes_calls_cli_without_auto_start(env: dict[str, str]) -> None:
    runner = FakeRunner(stdout=(FIXTURES / "wezterm_list_basic.json").read_text())
    panes = list_panes(env, runner=runner)
    assert len(panes) == 4
    cmd, kwargs = runner.calls[0]
    assert list(cmd) == ["wezterm", "cli", "--no-auto-start", "list", "--format", "json"]
    passed_env = kwargs["env"]
    assert isinstance(passed_env, dict)
    assert passed_env["WEZTERM_UNIX_SOCKET"] == env["WEZTERM_UNIX_SOCKET"]
    assert kwargs["timeout"] == 3


def test_list_panes_without_socket(tmp_path: Path) -> None:
    with pytest.raises(WeztermUnavailable, match="no WezTerm GUI socket found"):
        list_panes({"XDG_RUNTIME_DIR": str(tmp_path)}, runner=FakeRunner())


@pytest.mark.parametrize(
    ("runner", "reason"),
    [
        (FakeRunner(exc=FileNotFoundError("wezterm")), "wezterm is not installed"),
        (FakeRunner(exc=subprocess.TimeoutExpired("wezterm", 3)), "wezterm cli timed out"),
        (
            FakeRunner(returncode=1, stderr="WARN x\nERROR failed to connect\n"),
            "ERROR failed to connect",
        ),
        (FakeRunner(returncode=2), "wezterm cli exited with 2"),
        (FakeRunner(stdout="not json"), "unexpected wezterm cli output"),
        (FakeRunner(stdout='[{"title": "no ids"}]'), "unexpected wezterm cli output"),
    ],
)
def test_list_panes_errors(env: dict[str, str], runner: FakeRunner, reason: str) -> None:
    with pytest.raises(WeztermUnavailable, match=reason):
        list_panes(env, runner=runner)


def test_activate_pane_runs_cli(env: dict[str, str]) -> None:
    runner = FakeRunner(stdout="")
    activate_pane(7, env, runner=runner)
    cmd, kwargs = runner.calls[0]
    assert list(cmd) == ["wezterm", "cli", "--no-auto-start", "activate-pane", "--pane-id", "7"]
    passed_env = kwargs["env"]
    assert isinstance(passed_env, dict)
    assert passed_env["WEZTERM_UNIX_SOCKET"] == env["WEZTERM_UNIX_SOCKET"]


def test_activate_pane_errors(env: dict[str, str], tmp_path: Path) -> None:
    with pytest.raises(WeztermUnavailable, match="pane 7 not found"):
        activate_pane(7, env, runner=FakeRunner(returncode=1, stderr="ERROR pane 7 not found\n"))
    with pytest.raises(WeztermUnavailable, match="no WezTerm GUI socket found"):
        activate_pane(7, {"XDG_RUNTIME_DIR": str(tmp_path)}, runner=FakeRunner())
