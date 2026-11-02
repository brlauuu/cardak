import itertools
import json
import shutil
import socket
import threading
import time
from pathlib import Path

import pytest

from cardak.collectors.docker import (
    BACKOFF_FIRST,
    BACKOFF_MAX,
    Container,
    DockerSampler,
    DockerTimeout,
    DockerUnavailable,
    list_containers,
    parse_containers,
    read_cgroup,
)

FIX = Path(__file__).parent / "fixtures" / "docker"
WEBSHOP = "04402d25c9297d9c3dde900f38eeaf33f10f4526da7b24b789a80ff6eac3f9f6"


def fixture_containers() -> list[Container]:
    return parse_containers(json.loads((FIX / "containers.json").read_text()))


def test_parse_containers_fixture() -> None:
    cs = {c.name: c for c in fixture_containers()}
    assert cs["webshop-db-1"].compose_dir == Path("/home/user/repos/webshop")
    assert cs["webshop-db-1"].id == WEBSHOP
    assert cs["api-gw-cache"].mounts == (Path("/home/user/repos/api-gw/data"),)
    assert cs["scratch"].compose_dir is None and cs["scratch"].mounts == ()


def test_parse_containers_ignores_volume_mounts_and_bad_entries() -> None:
    data = [
        {
            "Id": "a",
            "Names": ["/x"],
            "Labels": None,
            "Mounts": [{"Type": "volume", "Source": "/var/lib/docker/volumes/v"}],
        },
        "junk",
        {"Names": ["/no-id"]},
    ]
    assert parse_containers(data) == [Container("a", "x", None, ())]
    assert parse_containers({"message": "not a list"}) == []


def test_list_containers_unreachable(tmp_path: Path) -> None:
    with pytest.raises(DockerUnavailable):
        list_containers(tmp_path / "docker.sock")


def _serve_once(path: Path, response: bytes) -> threading.Thread:
    server = socket.socket(socket.AF_UNIX)
    server.bind(str(path))
    server.listen(1)

    def run() -> None:
        conn, _ = server.accept()
        conn.recv(65536)
        conn.sendall(response)
        conn.close()
        server.close()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def test_list_containers_over_the_socket(tmp_path: Path) -> None:
    body = (FIX / "containers.json").read_bytes()
    head = f"HTTP/1.1 200 OK\r\nContent-Length: {len(body)}\r\n\r\n".encode()
    thread = _serve_once(tmp_path / "d.sock", head + body)
    names = sorted(c.name for c in list_containers(tmp_path / "d.sock"))
    thread.join(1)
    assert names == ["api-gw-cache", "scratch", "webshop-db-1"]


def test_list_containers_error_status(tmp_path: Path) -> None:
    thread = _serve_once(
        tmp_path / "d.sock", b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n"
    )
    with pytest.raises(DockerUnavailable):
        list_containers(tmp_path / "d.sock")
    thread.join(1)


def test_list_containers_bad_json(tmp_path: Path) -> None:
    thread = _serve_once(tmp_path / "d.sock", b"HTTP/1.1 200 OK\r\nContent-Length: 3\r\n\r\n{{{")
    with pytest.raises(DockerUnavailable):
        list_containers(tmp_path / "d.sock")
    thread.join(1)


def test_read_cgroup(tmp_path: Path) -> None:
    d = tmp_path / "g"
    d.mkdir()
    (d / "memory.current").write_text("1048576\n")
    (d / "cpu.stat").write_text("usage_usec 2500000\nuser_usec 1\n")
    assert read_cgroup(d) == (1048576, 2500000)
    assert read_cgroup(tmp_path / "gone") is None
    (d / "cpu.stat").write_text("user_usec 1\n")
    assert read_cgroup(d) is None


def test_first_container_cpu_is_none_then_delta(tmp_path: Path) -> None:
    root = tmp_path / "cgroup"
    shutil.copytree(FIX / "cgroup", root)
    ticks = iter([0.0, 2.0])
    sampler = DockerSampler(list_fn=fixture_containers, cgroup_root=root, clock=lambda: next(ticks))
    first = {w.name: w for w in sampler.sample()}
    assert first["webshop-db-1"].cpu_pct is None
    assert first["webshop-db-1"].rss == 2 * 2**30
    assert first["webshop-db-1"].compose_dir == Path("/home/user/repos/webshop")
    stat = root / f"docker-{WEBSHOP}.scope" / "cpu.stat"
    stat.write_text(f"usage_usec {59122 + 1_000_000}\n")
    second = {w.name: w for w in sampler.sample()}
    assert second["webshop-db-1"].cpu_pct == pytest.approx(50.0)
    assert second["scratch"].cpu_pct == pytest.approx(0.0)


def test_builds_are_found_without_the_api() -> None:
    def down() -> list[Container]:
        raise DockerUnavailable("no socket")

    work = DockerSampler(list_fn=down, cgroup_root=FIX / "cgroup").sample()
    assert [(w.kind, w.name, w.rss) for w in work] == [("build", "build", 69349376)]


def test_vanished_cgroup_is_skipped() -> None:
    gone = Container("deadbeef", "gone", None, ())  # no docker-deadbeef.scope in the fixture
    work = DockerSampler(list_fn=lambda: [gone], cgroup_root=FIX / "cgroup").sample()
    assert all(w.id != "deadbeef" for w in work)
    assert [w.kind for w in work] == ["build"]


def test_missing_cgroup_root_gives_nothing(tmp_path: Path) -> None:
    assert DockerSampler(list_fn=lambda: [], cgroup_root=tmp_path / "none").sample() == []


def test_read_cgroup_leaves_out_inactive_page_cache(tmp_path: Path) -> None:
    d = tmp_path / "g"
    d.mkdir()
    (d / "memory.current").write_text(f"{3 * 2**30}\n")
    (d / "memory.stat").write_text(f"anon 1000\nfile 5000\ninactive_file {2 * 2**30}\n")
    (d / "cpu.stat").write_text("usage_usec 1\n")
    assert read_cgroup(d) == (2**30, 1)  # as `docker stats` counts it
    (d / "memory.stat").write_text(f"inactive_file {4 * 2**30}\n")
    assert read_cgroup(d) == (0, 1)


def test_unreachable_docker_closes_its_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    closed: list[bool] = []
    real_socket = socket.socket

    class Tracked(real_socket):  # type: ignore[misc, valid-type]
        def close(self) -> None:
            closed.append(True)
            super().close()

    monkeypatch.setattr(socket, "socket", Tracked)
    with pytest.raises(DockerUnavailable):
        list_containers(tmp_path / "docker.sock")
    assert closed  # not left for the garbage collector


def test_a_hung_socket_times_out(tmp_path: Path) -> None:
    """A daemon that accepts and never answers (#107): DockerTimeout, within the timeout."""
    path = tmp_path / "d.sock"
    server = socket.socket(socket.AF_UNIX)
    server.bind(str(path))
    server.listen(1)
    try:
        start = time.monotonic()
        with pytest.raises(DockerTimeout):
            list_containers(path, timeout=0.1)
        assert time.monotonic() - start < 1
    finally:
        server.close()


class _Daemon:
    """A fake listing on a fake clock: each call takes the next answer, then [] for good."""

    def __init__(self, *answers: list[Container] | Exception) -> None:
        self.now = 0.0
        self.calls: list[float] = []
        self.answers = list(answers)

    def clock(self) -> float:
        return self.now

    def listing(self) -> list[Container]:
        self.calls.append(self.now)
        answer = self.answers.pop(0) if self.answers else []
        if isinstance(answer, Exception):
            raise answer
        return answer

    def sampler(self) -> DockerSampler:
        return DockerSampler(list_fn=self.listing, cgroup_root=FIX / "cgroup", clock=self.clock)


def test_a_timeout_backs_off_and_keeps_the_last_containers() -> None:
    """A hung daemon is asked again only after a growing pause, not on every poll (#107)."""
    hung = DockerTimeout("timed out")
    daemon = _Daemon(fixture_containers(), hung, hung)
    sampler = daemon.sampler()
    seen: dict[float, set[str]] = {}
    for t in range(0, 96, 2):  # a poll every 2 s
        daemon.now = float(t)
        seen[t] = {w.name for w in sampler.sample()}
    # answered at 0, hung at 2, left alone until 32, hung again, left alone until 92
    assert daemon.calls == [0.0, 2.0, 2.0 + BACKOFF_FIRST, 2.0 + 3 * BACKOFF_FIRST, 94.0]
    assert all("webshop-db-1" in seen[t] for t in range(0, 92, 2))  # cgroups still read
    assert "webshop-db-1" not in seen[92]  # the daemon answered: nothing running


def test_backoff_is_capped_and_resets_after_an_answer() -> None:
    daemon = _Daemon(*[DockerTimeout("timed out")] * 10)
    sampler = daemon.sampler()
    for t in range(0, 4000, 2):
        daemon.now = float(t)
        sampler.sample()
    gaps = [b - a for a, b in itertools.pairwise(daemon.calls)]
    assert gaps[:10] == [30.0, 60.0, 120.0, 240.0, *[BACKOFF_MAX] * 6]
    assert set(gaps[10:]) == {2.0}  # it answered: asked every poll again


def test_an_unreachable_daemon_is_asked_every_poll() -> None:
    """No socket fails at once and costs nothing, so a Docker that starts shows up at once."""
    down = DockerUnavailable("no socket")
    daemon = _Daemon(down, down, down)
    sampler = daemon.sampler()
    for t in (0, 2, 4):
        daemon.now = float(t)
        sampler.sample()
    assert daemon.calls == [0.0, 2.0, 4.0]
