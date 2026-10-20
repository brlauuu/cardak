import os
import subprocess
import time

from cardak.collectors.processes import (
    PaneProcs,
    ProcessSampler,
    ProcInfo,
    PsutilTable,
    is_claude,
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


def test_psutil_table_reads_this_process() -> None:
    table = PsutilTable()
    first = {p.pid: p for p in table.snapshot()}
    me = first[os.getpid()]
    assert me.ppid == os.getppid()
    assert me.pgid == os.getpgrp()
    assert me.cpu_pct is None
    assert me.rss > 0
    second = {p.pid: p for p in table.snapshot()}
    assert second[os.getpid()].cpu_pct is not None


def test_psutil_table_sees_terminals_opened_after_the_first_snapshot() -> None:
    # psutil memoizes its tty map on first use, so a pane opened later would have no tty.
    table = PsutilTable()
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
