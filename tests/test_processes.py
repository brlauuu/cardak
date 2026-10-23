import os
import subprocess
import time
from pathlib import Path

import pytest

from cardak.collectors.processes import (
    PaneProcs,
    ProcessSampler,
    ProcInfo,
    ProcTable,
    is_claude,
    is_docker_build,
    is_mcp_server,
    summarize,
)

T = "/dev/pts/7"
GIB = 2**30


def proc(
    pid: int,
    ppid: int = 1,
    *,
    tty: str | None = None,
    name: str = "zsh",
    cmdline: tuple[str, ...] | None = None,
    pgid: int | None = None,
    tpgid: int = -1,
    ctime: float = 0.0,
    cpu: float | None = 0.0,
    rss: int = 0,
) -> ProcInfo:
    return ProcInfo(
        pid=pid,
        ppid=ppid,
        tty=tty,
        pgid=pid if pgid is None else pgid,
        tpgid=tpgid,
        name=name,
        cmdline=(name,) if cmdline is None else cmdline,
        create_time=ctime,
        cpu_pct=cpu,
        rss=rss,
    )


def test_idle_shell() -> None:
    procs = [proc(10, tty=T, tpgid=10, cpu=0.5, rss=5_000_000)]
    assert summarize(procs, [T]) == {
        T: PaneProcs(
            fg_name="zsh",
            fg_pid=10,
            claude_pid=None,
            cpu_pct=0.5,
            rss=5_000_000,
            n_procs=1,
            mcp_cpu_pct=0.0,
            mcp_rss=0,
        )
    }


def test_build_under_claude_counts_as_work_and_mcp_is_split() -> None:
    procs = [
        proc(10, tty=T, tpgid=20, cpu=0.0, rss=1),
        proc(20, 10, tty=T, name="claude", tpgid=20, cpu=4.0, rss=300),
        proc(30, 20, name="zsh", cpu=0.0, rss=2),
        proc(31, 30, name="cargo", cmdline=("cargo", "build"), cpu=290.0, rss=5 * GIB),
        proc(
            40,
            20,
            tty=T,
            name="npm exec @playw",
            cmdline=("npm exec @playwright/mcp@latest",),
            tpgid=20,
            pgid=20,
            cpu=3.0,
            rss=100,
        ),
        proc(41, 40, name="node", cmdline=("node", "/x/mcp/server.js"), cpu=1.0, rss=50),
        proc(42, 20, name="uvx", cmdline=("uvx", "some-server"), cpu=0.5, rss=10),
        proc(99, 1, name="other", cpu=50.0, rss=999),
    ]
    out = summarize(procs, [T])[T]
    assert (out.fg_name, out.fg_pid, out.claude_pid) == ("claude", 20, 20)
    assert out.cpu_pct == 294.0
    assert out.rss == 1 + 300 + 2 + 5 * GIB
    assert out.n_procs == 4
    assert (out.mcp_cpu_pct, out.mcp_rss) == (4.5, 160)


def test_foreground_falls_back_to_newest_when_tpgid_unknown() -> None:
    procs = [proc(10, tty=T, ctime=1.0), proc(11, 10, tty=T, name="vim", ctime=2.0)]
    assert summarize(procs, [T])[T].fg_name == "vim"


def test_foreground_prefers_group_leader_in_a_pipeline() -> None:
    procs = [
        proc(10, tty=T, tpgid=20),
        proc(21, 10, tty=T, name="less", pgid=20, tpgid=20, ctime=3.0),
        proc(20, 10, tty=T, name="cat", pgid=20, tpgid=20, ctime=2.0),
    ]
    assert summarize(procs, [T])[T].fg_name == "cat"


def test_cpu_none_only_when_every_process_is_new() -> None:
    assert summarize([proc(10, tty=T, cpu=None)], [T])[T].cpu_pct is None
    both = [proc(10, tty=T, cpu=None), proc(11, 10, cpu=2.0)]
    assert summarize(both, [T])[T].cpu_pct == 2.0


def test_unknown_tty_is_empty() -> None:
    assert summarize([], ["/dev/pts/99"])["/dev/pts/99"] == PaneProcs(
        fg_name=None,
        fg_pid=None,
        claude_pid=None,
        cpu_pct=None,
        rss=0,
        n_procs=0,
        mcp_cpu_pct=0.0,
        mcp_rss=0,
    )


def test_self_parent_does_not_loop() -> None:
    procs = [proc(5, 5, tty=T, tpgid=5), proc(6, 5, cpu=1.0)]
    assert summarize(procs, [T])[T].n_procs == 2


def test_is_claude_variants() -> None:
    assert is_claude(proc(1, name="claude", cmdline=("claude",)))
    assert is_claude(
        proc(
            1,
            name="node",
            cmdline=("node", "/usr/lib/node_modules/@anthropic-ai/claude-code/cli.js"),
        )
    )
    assert not is_claude(proc(1, name="node", cmdline=("node", "server.js")))
    assert is_claude(proc(1, name="claude", cmdline=()))


def test_is_mcp_server_variants() -> None:
    assert is_mcp_server(proc(1, cmdline=("npm", "exec", "@x/y")))
    assert is_mcp_server(proc(1, cmdline=("npm exec @plaud/mcp",)))
    assert is_mcp_server(proc(1, cmdline=("npx", "-y", "thing")))
    assert is_mcp_server(proc(1, cmdline=("/usr/bin/python", "-m", "my_MCP_server")))
    assert not is_mcp_server(proc(1, cmdline=("bash", "-c", "cargo build")))
    assert not is_mcp_server(proc(1, name="zsh", cmdline=()))


class FakeTable:
    def __init__(self, procs: list[ProcInfo]) -> None:
        self.procs = procs

    def snapshot(self) -> list[ProcInfo]:
        return self.procs


def test_sampler_uses_table() -> None:
    sampler = ProcessSampler(FakeTable([proc(10, tty=T, tpgid=10)]))
    assert sampler.sample([T])[T].fg_pid == 10


def test_proc_table_reads_this_process() -> None:
    table = ProcTable()
    first = {p.pid: p for p in table.snapshot()}
    me = first[os.getpid()]
    assert me.ppid == os.getppid()
    assert me.pgid == os.getpgrp()
    assert me.cpu_pct is None
    assert me.rss > 0
    second = {p.pid: p for p in table.snapshot()}
    assert second[os.getpid()].cpu_pct is not None


def test_proc_table_sees_terminals_opened_after_the_first_snapshot() -> None:
    # psutil memoizes its tty map on first use, so a pane opened later would have no tty.
    table = ProcTable()
    table.snapshot()
    master, slave = os.openpty()
    path = os.ttyname(slave)
    child = subprocess.Popen(["sh", "-c", f"exec sleep 10 < {path}"], start_new_session=True)
    try:
        deadline = time.monotonic() + 3
        found = None
        while found is None and time.monotonic() < deadline:
            found = next(
                (p for p in table.snapshot() if p.pid == child.pid and p.name == "sleep"), None
            )
        assert found is not None
        assert found.tty == path
    finally:
        child.kill()
        child.wait()
        os.close(master)
        os.close(slave)


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (("docker", "build", "."), True),
        (("docker", "buildx", "build", "-t", "x", "."), True),
        (("docker", "image", "build", "."), True),
        (("docker", "builder", "build", "."), True),
        (("docker", "compose", "-f", "c.yml", "build"), True),
        (("docker-compose", "build"), True),
        (("/usr/bin/docker", "build", "."), True),
        (("docker", "run", "busybox"), False),
        (("docker", "compose", "up"), False),
        (("docker", "run", "img", "build"), False),
        (("make", "build"), False),
        ((), False),
    ],
)
def test_is_docker_build(argv: tuple[str, ...], expected: bool) -> None:
    assert is_docker_build(proc(5, name=argv[0] if argv else "?", cmdline=argv)) is expected


def test_pane_with_docker_build_client() -> None:
    procs = [
        proc(10, tty=T, name="zsh"),
        proc(11, 10, tty=T, name="docker", cmdline=("docker", "build", ".")),
    ]
    assert summarize(procs, [T])[T].docker_build is True
    assert summarize([proc(10, tty=T, name="zsh")], [T])[T].docker_build is False


def _fake_proc(
    root: Path,
    pid: int,
    comm: str,
    argv: tuple[str, ...],
    *,
    ppid: int = 1,
    ticks: int = 0,
    start: int = 100,
    rss_pages: int = 256,
    tty_nr: int = 0,
) -> None:
    d = root / str(pid)
    d.mkdir(parents=True, exist_ok=True)
    # pid (comm) state ppid pgrp session tty_nr tpgid flags minflt cminflt majflt cmajflt
    # utime stime cutime cstime priority nice threads itreal starttime vsize rss
    fields = [
        "S",
        ppid,
        pid,
        pid,
        tty_nr,
        -1,
        0,
        0,
        0,
        0,
        0,
        ticks,
        0,
        0,
        0,
        20,
        0,
        1,
        0,
        start,
        1000,
        rss_pages,
    ]
    (d / "stat").write_text(f"{pid} ({comm}) " + " ".join(map(str, fields)) + " 0 0\n")
    (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + (b"\0" if argv else b""))


def _proc_root(tmp_path: Path) -> Path:
    root = tmp_path / "proc"
    root.mkdir()
    (root / "stat").write_text("cpu  1 2 3\nbtime 1000\n")
    (root / "self").mkdir()
    return root


def test_proc_table_reads_stat_and_cmdline(tmp_path: Path) -> None:
    root = _proc_root(tmp_path)
    _fake_proc(root, 10, "zsh", ("-zsh",), ticks=100)
    _fake_proc(root, 11, "weird) name", ("python3", "x.py"), ppid=10, ticks=50)
    clock = iter([0.0, 2.0])
    table = ProcTable(
        root, tmp_path / "dev", clock=lambda: next(clock), clk_tck=100, page_size=4096
    )
    first = {p.pid: p for p in table.snapshot()}
    assert first[11].name == "weird) name" and first[11].ppid == 10
    assert first[11].cmdline == ("python3", "x.py") and first[11].rss == 256 * 4096
    assert first[11].create_time == 1000 + 100 / 100 and first[11].cpu_pct is None
    _fake_proc(root, 11, "weird) name", ("python3", "x.py"), ppid=10, ticks=150)
    second = {p.pid: p for p in table.snapshot()}
    assert second[11].cpu_pct == pytest.approx(50.0)  # 1 s of CPU over 2 s
    assert second[10].cpu_pct == pytest.approx(0.0)


def test_proc_table_skips_vanished_and_unreadable(tmp_path: Path) -> None:
    root = _proc_root(tmp_path)
    _fake_proc(root, 10, "zsh", ("zsh",))
    (root / "12").mkdir()  # no stat: exited between listdir and read
    (root / "not-a-pid").mkdir()
    table = ProcTable(root, tmp_path / "dev", clock=lambda: 0.0, clk_tck=100, page_size=4096)
    assert [p.pid for p in table.snapshot()] == [10]


def test_proc_table_rereads_cmdline_after_exec_and_while_young(tmp_path: Path) -> None:
    root = _proc_root(tmp_path)
    now = 1000.0 + 1.0 + 60.0  # the process started 60 s ago: its argv has settled
    _fake_proc(root, 10, "sh", ("sh", "-c", "x"))
    table = ProcTable(
        root, tmp_path / "dev", clock=lambda: 0.0, clk_tck=100, page_size=4096, wall=lambda: now
    )
    assert table.snapshot()[0].cmdline == ("sh", "-c", "x")
    (root / "10" / "cmdline").write_bytes(b"rewritten\0")
    assert table.snapshot()[0].cmdline == ("sh", "-c", "x")  # cached
    _fake_proc(root, 10, "sleep", ("sleep", "10"))  # exec: same pid and start, new comm
    assert table.snapshot()[0].cmdline == ("sleep", "10")
    young = ProcTable(
        root,
        tmp_path / "dev",
        clock=lambda: 0.0,
        clk_tck=100,
        page_size=4096,
        wall=lambda: 1000.0 + 1.0 + 5.0,
    )
    young.snapshot()
    (root / "10" / "cmdline").write_bytes(b"npm exec @x/mcp\0")
    assert young.snapshot()[0].cmdline == ("npm exec @x/mcp",)


def test_proc_table_long_comm_takes_the_name_from_argv(tmp_path: Path) -> None:
    root = _proc_root(tmp_path)
    _fake_proc(root, 10, "wezterm-mux-ser", ("/usr/bin/wezterm-mux-server", "--daemonize"))
    table = ProcTable(root, tmp_path / "dev", clock=lambda: 0.0, clk_tck=100, page_size=4096)
    assert table.snapshot()[0].name == "wezterm-mux-server"


def test_proc_table_matches_psutil_for_this_process() -> None:
    import psutil

    me = {p.pid: p for p in ProcTable().snapshot()}[os.getpid()]
    ps = psutil.Process()
    assert me.name == ps.name() and me.cmdline == tuple(ps.cmdline())
    assert me.create_time == pytest.approx(ps.create_time(), abs=0.05)
    assert abs(me.rss - ps.memory_info().rss) < 32 * 2**20
