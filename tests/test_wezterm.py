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
    gui_processes,
    list_panes,
    parse_cwd,
    parse_panes,
    send_text,
    spawn,
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


def test_send_text_args(env: dict[str, str]) -> None:
    runner = FakeRunner(stdout="")
    send_text(12, "/checkpoint\r", env, runner=runner)
    cmd, _ = runner.calls[0]
    assert list(cmd) == [
        "wezterm", "cli", "--no-auto-start", "send-text", "--pane-id", "12", "--no-paste",
        "/checkpoint\r",
    ]  # fmt: skip


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


def test_gui_processes_maps_scopes(tmp_path: Path) -> None:
    d = _runtime(tmp_path)
    pid = os.getpid()
    (d / f"gui-sock-{pid}").touch()
    (d / f"gui-sock-{_dead_pid()}").touch()
    (d / "sock").touch()
    proc = tmp_path / "proc"
    env = {"XDG_RUNTIME_DIR": str(tmp_path)}
    assert gui_processes(env, proc) == {pid: None}
    scope = "app-org.wezfurlong.wezterm-0123456789abcdef0123456789abcdef.scope"
    (proc / str(pid)).mkdir(parents=True)
    (proc / str(pid) / "cgroup").write_text(
        f"0::/user.slice/user-1000.slice/user@1000.service/app.slice/{scope}\n"
    )
    assert gui_processes(env, proc) == {pid: scope}
    (proc / str(pid) / "cgroup").write_text("0::/user.slice/user-1000.slice/session-2.scope/x\n")
    assert gui_processes(env, proc) == {pid: None}
    assert gui_processes({}, proc) == {}


def test_vanished_gui_sock_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    d = _runtime(tmp_path)
    gone, live = d / f"gui-sock-{os.getppid()}", d / f"gui-sock-{os.getpid()}"
    gone.touch()
    live.touch()
    real_stat = Path.stat

    def stat(self: Path, **kwargs: object) -> os.stat_result:
        if self == gone:  # its GUI exited between the listing and the stat (#15)
            raise FileNotFoundError(str(self))
        return real_stat(self, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "stat", stat)
    assert find_socket({"XDG_RUNTIME_DIR": str(tmp_path)}) == live


def test_spawn_next_to_a_pane(env: dict[str, str]) -> None:
    runner = FakeRunner(stdout="42\n")
    assert spawn(Path("/r/web"), 7, env, runner=runner) == 42
    cmd, _ = runner.calls[0]
    assert list(cmd) == [
        "wezterm", "cli", "--no-auto-start", "spawn", "--cwd", "/r/web", "--pane-id", "7",
    ]  # fmt: skip


def test_spawn_without_a_pane_uses_the_callers_window(env: dict[str, str]) -> None:
    runner = FakeRunner(stdout="42\n")
    spawn(Path("/r/web"), None, {**env, "WEZTERM_PANE": "3"}, runner=runner)
    assert list(runner.calls[0][0])[-2:] == ["--cwd", "/r/web"]


def test_spawn_outside_wezterm_opens_a_window(env: dict[str, str]) -> None:
    runner = FakeRunner(stdout="42\n")
    spawn(Path("/r/web"), None, env, runner=runner)
    assert list(runner.calls[0][0])[-1] == "--new-window"


def test_spawn_rejects_odd_output(env: dict[str, str]) -> None:
    with pytest.raises(WeztermUnavailable, match="unexpected"):
        spawn(Path("/r/web"), 7, env, runner=FakeRunner(stdout="nope"))
