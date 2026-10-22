from pathlib import Path

import pytest

from cardak.collectors.machine import MachineSampler, parse_meminfo

FIXTURES = Path(__file__).parent / "fixtures" / "proc"


def test_parse_meminfo_fixture() -> None:
    total, avail, swap_total, swap_free = parse_meminfo((FIXTURES / "meminfo").read_text())
    assert total == 32 * 2**30
    assert total > avail > 0 and swap_total >= swap_free >= 0
    assert total % 1024 == 0  # kB converted to bytes


def test_parse_meminfo_missing_field_raises() -> None:
    with pytest.raises(ValueError):
        parse_meminfo("MemTotal: 1000 kB\n")


def test_first_cpu_sample_is_none() -> None:
    calls = iter([0.0, 37.5])
    s = MachineSampler(meminfo=FIXTURES / "meminfo", cpu=lambda: next(calls), n_cpus=16)
    first = s.sample()
    assert first is not None and first.cpu_pct is None
    second = s.sample()
    assert second is not None
    assert second.cpu_pct == 37.5 and second.n_cpus == 16
    assert second.mem_used == second.mem_total - second.mem_available


def test_unreadable_meminfo_is_none(tmp_path: Path) -> None:
    assert MachineSampler(meminfo=tmp_path / "nope", cpu=lambda: 0.0).sample() is None


def test_defaults_read_the_live_machine() -> None:
    sample = MachineSampler().sample()
    assert sample is not None and sample.mem_total > 0 and sample.n_cpus >= 1
