import json
import shutil
import socket
import threading
from pathlib import Path

import pytest

from cardak.collectors.docker import (
    Container,
    DockerSampler,
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
