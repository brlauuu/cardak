from cardak.collectors.machine import MachineSample
from cardak.core.config import ResourceSettings
from cardak.core.resources import GIB, default_entries, derive, is_over, memory_level

T32 = derive(ResourceSettings(), 32 * GIB, 16)


def machine(available_gib: float, swap_total: int = GIB, swap_free: int = GIB) -> MachineSample:
    return MachineSample(32 * GIB, int(available_gib * GIB), swap_total, swap_free, 10.0, 16)


def test_defaults_for_a_16_core_32_gib_machine() -> None:
    t = T32
    assert t.ram_warn_available == int(0.15 * 32 * GIB)
    assert t.ram_critical_available == int(0.07 * 32 * GIB)
    assert t.pane_ram == 8 * GIB
    assert t.growth_per_min == GIB
    assert t.pane_cpu_pct == 800.0 and t.machine_cpu_pct == 90.0
    assert t.sustain_s == 120.0 and t.repeat_s == 600.0


def test_settings_override_defaults() -> None:
    t = derive(
        ResourceSettings(pane_ram_gb=4.0, repeat_min=1, ram_warn_available_gb=6), 32 * GIB, 16
    )
    assert t.pane_ram == 4 * GIB and t.repeat_s == 60.0 and t.ram_warn_available == 6 * GIB


def test_memory_level() -> None:
    assert memory_level(machine(10), T32) is None
    assert memory_level(machine(4), T32) == "warning"
    assert memory_level(machine(2), T32) == "critical"
    assert memory_level(machine(10, swap_free=0), T32) == "critical"
    assert memory_level(machine(10, swap_total=0, swap_free=0), T32) is None


def test_is_over() -> None:
    assert is_over(9 * GIB, 10.0, T32)
    assert is_over(0, 801.0, T32)
    assert not is_over(7 * GIB, None, T32)
    assert not is_over(8 * GIB, 800.0, T32)  # at the line is not over it


def test_default_entries_for_32_gib_16_cores() -> None:
    entries = {k: (v, c) for k, v, c in default_entries(32 * GIB, 16)}
    assert list(entries) == [
        "ram_warn_available_gb", "ram_critical_available_gb", "pane_ram_gb", "growth_gb_per_min",
        "pane_cpu_pct", "machine_cpu_pct", "sustain_s", "repeat_min",
    ]  # fmt: skip
    assert entries["ram_warn_available_gb"] == (4.8, "15% of 32 GB")
    assert entries["ram_critical_available_gb"] == (2.2, "7% of 32 GB")
    assert entries["pane_ram_gb"] == (8.0, "25% of 32 GB")
    assert entries["pane_cpu_pct"] == (800.0, "half of 16 cores")
    assert entries["growth_gb_per_min"] == (1.0, "")


def test_default_entries_match_derive() -> None:
    values = {k: v for k, v, _ in default_entries(32 * GIB, 16)}
    from_file = derive(ResourceSettings(**values), 32 * GIB, 16)
    assert from_file.pane_ram == T32.pane_ram and from_file.pane_cpu_pct == T32.pane_cpu_pct
