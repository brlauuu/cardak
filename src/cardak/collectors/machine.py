"""Machine-wide memory (/proc/meminfo) and CPU: the only reader of either."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import psutil

_FIELDS = ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree")


@dataclass(frozen=True)
class MachineSample:
    mem_total: int  # bytes
    mem_available: int
    swap_total: int
    swap_free: int
    cpu_pct: float | None  # whole machine, 0–100; None on the first sample
    n_cpus: int

    @property
    def mem_used(self) -> int:
        return self.mem_total - self.mem_available


def parse_meminfo(text: str) -> tuple[int, int, int, int]:
    """(total, available, swap total, swap free) in bytes."""
    values: dict[str, int] = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        if key in _FIELDS:
            values[key] = int(rest.split()[0]) * 1024
    missing = [f for f in _FIELDS if f not in values]
    if missing:
        raise ValueError(f"meminfo lacks {', '.join(missing)}")
    total, available, swap_total, swap_free = (values[f] for f in _FIELDS)
    return total, available, swap_total, swap_free


class MachineSampler:
    def __init__(
        self,
        meminfo: Path = Path("/proc/meminfo"),
        cpu: Callable[[], float] = lambda: psutil.cpu_percent(None),
        n_cpus: int | None = None,
    ) -> None:
        self._meminfo = meminfo
        self._cpu = cpu
        self._n_cpus = n_cpus or psutil.cpu_count() or 1
        self._primed = False  # cpu_percent(None)'s first value is meaningless

    def sample(self) -> MachineSample | None:
        cpu = self._cpu()
        cpu_pct = cpu if self._primed else None
        self._primed = True
        try:
            total, available, swap_total, swap_free = parse_meminfo(self._meminfo.read_text())
        except (OSError, ValueError):
            return None
        return MachineSample(total, available, swap_total, swap_free, cpu_pct, self._n_cpus)
