"""Resource thresholds: [resources] where set, else derived from the machine. Pure."""

from dataclasses import dataclass
from typing import Literal

from cardak.collectors.machine import MachineSample
from cardak.core.config import ResourceSettings

GIB = 2**30  # "GB" in [resources] keys means GiB, as the deck prints memory
MemoryLevel = Literal["warning", "critical"]


@dataclass(frozen=True)
class Thresholds:
    ram_warn_available: int  # bytes
    ram_critical_available: int
    pane_ram: int
    growth_per_min: int
    pane_cpu_pct: float  # deck percent, 100 = one core
    machine_cpu_pct: float  # 0–100
    sustain_s: float
    repeat_s: float


def derive(settings: ResourceSettings, mem_total: int, n_cpus: int) -> Thresholds:
    def gib(value: float | None, share: float) -> int:
        return int(value * GIB) if value is not None else int(share * mem_total)

    s = settings
    return Thresholds(
        ram_warn_available=gib(s.ram_warn_available_gb, 0.15),
        ram_critical_available=gib(s.ram_critical_available_gb, 0.07),
        pane_ram=gib(s.pane_ram_gb, 0.25),
        growth_per_min=int((s.growth_gb_per_min or 1.0) * GIB),
        pane_cpu_pct=s.pane_cpu_pct or 50.0 * n_cpus,
        machine_cpu_pct=s.machine_cpu_pct or 90.0,
        sustain_s=s.sustain_s or 120.0,
        repeat_s=(s.repeat_min or 10.0) * 60,
    )


def default_entries(mem_total: int, n_cpus: int) -> list[tuple[str, float, str]]:
    """What `cardak resources install` writes into [resources]: (key, value, how it was derived)."""
    total = mem_total / GIB
    of = f"of {total:.0f} GB"
    return [
        ("ram_warn_available_gb", round(0.15 * total, 1), f"15% {of}"),
        ("ram_critical_available_gb", round(0.07 * total, 1), f"7% {of}"),
        ("pane_ram_gb", round(0.25 * total, 1), f"25% {of}"),
        ("growth_gb_per_min", 1.0, ""),
        ("pane_cpu_pct", 50.0 * n_cpus, f"half of {n_cpus} cores"),
        ("machine_cpu_pct", 90.0, ""),
        ("sustain_s", 120.0, ""),
        ("repeat_min", 10.0, ""),
    ]


def memory_level(m: MachineSample, t: Thresholds) -> MemoryLevel | None:
    swap_full = m.swap_total > 0 and m.swap_free == 0
    if m.mem_available < t.ram_critical_available or swap_full:
        return "critical"
    if m.mem_available < t.ram_warn_available:
        return "warning"
    return None


def is_over(rss: int, cpu_pct: float | None, t: Thresholds) -> bool:
    return rss > t.pane_ram or (cpu_pct or 0.0) > t.pane_cpu_pct
