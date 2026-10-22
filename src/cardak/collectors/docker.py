"""Docker, read-only: running containers from the Engine API, usage from cgroup v2 files.

The only module that talks to Docker. Containers live in
`system.slice/docker-<id>.scope`; BuildKit build steps in `system.slice/system.slice:docker:<id>`
while they run (systemd cgroup driver).
"""

import http.client
import json
import os
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

DOCKER_SOCK = Path("/var/run/docker.sock")
CGROUP_ROOT = Path("/sys/fs/cgroup/system.slice")
BUILD_PREFIX = "system.slice:docker:"
COMPOSE_DIR = "com.docker.compose.project.working_dir"


class DockerUnavailable(Exception):
    pass


@dataclass(frozen=True)
class Container:
    id: str
    name: str  # without the leading "/"
    compose_dir: Path | None  # com.docker.compose.project.working_dir
    mounts: tuple[Path, ...]  # bind mount sources only


@dataclass(frozen=True)
class DockerWork:
    kind: Literal["container", "build"]
    id: str
    name: str  # container name, or "build" for a build step
    rss: int  # memory.current
    cpu_pct: float | None  # None until a second sample of the same cgroup
    compose_dir: Path | None = None
    mounts: tuple[Path, ...] = ()


def _name(entry: dict[str, object], cid: str) -> str:
    names = entry.get("Names")
    first = names[0] if isinstance(names, list) and names else None
    return first.lstrip("/") if isinstance(first, str) else cid[:12]


def _compose_dir(entry: dict[str, object]) -> Path | None:
    labels = entry.get("Labels")
    value = labels.get(COMPOSE_DIR) if isinstance(labels, dict) else None
    return Path(value) if isinstance(value, str) else None


def _bind_mounts(entry: dict[str, object]) -> tuple[Path, ...]:
    mounts = entry.get("Mounts")
    if not isinstance(mounts, list):
        return ()
    return tuple(
        Path(m["Source"])
        for m in mounts
        if isinstance(m, dict) and m.get("Type") == "bind" and isinstance(m.get("Source"), str)
    )


def _container(entry: object) -> Container | None:
    if not isinstance(entry, dict):
        return None
    cid = entry.get("Id")
    if not isinstance(cid, str) or not cid:
        return None
    return Container(cid, _name(entry, cid), _compose_dir(entry), _bind_mounts(entry))


def parse_containers(data: object) -> list[Container]:
    """The body of GET /containers/json; entries that don't look like containers are skipped."""
    if not isinstance(data, list):
        return []
    return [c for entry in data if (c := _container(entry)) is not None]


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: Path, timeout: float) -> None:
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(str(self._path))
        self.sock = sock


def _default_sock() -> Path:
    host = os.environ.get("DOCKER_HOST", "")
    return Path(host.removeprefix("unix://")) if host.startswith("unix://") else DOCKER_SOCK


def list_containers(sock: Path | None = None, timeout: float = 0.5) -> list[Container]:
    conn = _UnixConnection(sock or _default_sock(), timeout)
    try:
        conn.request("GET", "/containers/json")
        response = conn.getresponse()
        body = response.read()
    except (OSError, http.client.HTTPException) as e:
        raise DockerUnavailable(str(e)) from e
    finally:
        conn.close()
    if response.status != 200:
        raise DockerUnavailable(f"HTTP {response.status}")
    try:
        return parse_containers(json.loads(body))
    except ValueError as e:
        raise DockerUnavailable(f"bad JSON: {e}") from e


def _inactive_file(path: Path) -> int:
    try:
        stat = (path / "memory.stat").read_text()
    except OSError:
        return 0
    for line in stat.splitlines():
        key, _, value = line.partition(" ")
        if key == "inactive_file" and value.strip().isdigit():
            return int(value)
    return 0


def read_cgroup(path: Path) -> tuple[int, int] | None:
    """(memory bytes, cpu usage_usec); None if the cgroup is gone or unreadable.

    Memory leaves out inactive page cache, as `docker stats` does: memory.current counts it,
    and the machine's "used" (MemTotal − MemAvailable) mostly doesn't.
    """
    try:
        memory = int((path / "memory.current").read_text())
        stat = (path / "cpu.stat").read_text()
    except (OSError, ValueError):
        return None
    for line in stat.splitlines():
        key, _, value = line.partition(" ")
        if key == "usage_usec" and value.strip().isdigit():
            return max(0, memory - _inactive_file(path)), int(value)
    return None


class DockerSampler:
    """Keeps the previous CPU counter per cgroup: CPU percent needs two samples."""

    def __init__(
        self,
        list_fn: Callable[[], list[Container]] = list_containers,
        cgroup_root: Path = CGROUP_ROOT,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._list = list_fn
        self._root = cgroup_root
        self._clock = clock
        self._last: dict[Path, tuple[int, float]] = {}

    def _builds(self) -> list[Path]:
        try:
            return sorted(p for p in self._root.iterdir() if p.name.startswith(BUILD_PREFIX))
        except OSError:
            return []

    def sample(self) -> list[DockerWork]:
        try:
            containers = self._list()
        except DockerUnavailable:
            containers = []
        now = self._clock()
        last, self._last = self._last, {}
        work: list[DockerWork] = []

        def add(path: Path, make: Callable[[int, float | None], DockerWork]) -> None:
            usage = read_cgroup(path)
            if usage is None:
                return
            memory, cpu_usec = usage
            self._last[path] = (cpu_usec, now)
            prev = last.get(path)
            cpu = None
            if prev is not None and now > prev[1]:
                cpu = (cpu_usec - prev[0]) / ((now - prev[1]) * 1e6) * 100
            work.append(make(memory, cpu))

        for c in containers:
            add(
                self._root / f"docker-{c.id}.scope",
                lambda rss, cpu, c=c: DockerWork(
                    "container", c.id, c.name, rss, cpu, c.compose_dir, c.mounts
                ),
            )
        for path in self._builds():
            build_id = path.name.removeprefix(BUILD_PREFIX)
            add(path, lambda rss, cpu, b=build_id: DockerWork("build", b, "build", rss, cpu))
        return work
