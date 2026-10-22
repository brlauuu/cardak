from cardak.collectors.machine import MachineSample
from cardak.core.config import ResourceSettings
from cardak.core.resources import GIB, derive, is_over, memory_level

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
