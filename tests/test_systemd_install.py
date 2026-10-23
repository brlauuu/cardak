import subprocess
from pathlib import Path

import pytest

from cardak.systemd.install import (
    DROPIN_DIR,
    MARKER,
    UNIT,
    disable,
    dropin_present,
    enable,
    is_ours,
    oom_advice,
    oom_policies,
    unit_dir,
    unit_text,
    write_unit,
)


class FakeRun:
    def __init__(self, *results: tuple[int, str, str]) -> None:
        self.results = list(results)
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        code, out, err = self.results.pop(0) if self.results else (0, "", "")
        return subprocess.CompletedProcess(argv, code, out, err)


def test_unit_dir_follows_xdg() -> None:
    assert unit_dir({"XDG_CONFIG_HOME": "/x"}) == Path("/x/systemd/user")
    assert unit_dir({"HOME": "/h"}) == Path("/h/.config/systemd/user")


def test_unit_text_runs_the_daemon() -> None:
    text = unit_text(Path("/home/user/.local/bin/cardak"))
    assert text.startswith(MARKER)
    assert "ExecStart=/home/user/.local/bin/cardak daemon\n" in text
    assert "Restart=on-failure" in text and "WantedBy=default.target" in text


def test_write_unit_refuses_a_foreign_file(tmp_path: Path) -> None:
    path = tmp_path / UNIT
    path.write_text("[Unit]\nDescription=someone else's\n")
    with pytest.raises(FileExistsError):
        write_unit(path, unit_text(Path("/bin/cardak")))
    write_unit(path, unit_text(Path("/bin/cardak")), force=True)
    assert is_ours(path)


def test_write_unit_replaces_its_own(tmp_path: Path) -> None:
    path = tmp_path / "sub" / UNIT
    write_unit(path, unit_text(Path("/a/cardak")))
    write_unit(path, unit_text(Path("/b/cardak")))
    assert "ExecStart=/b/cardak daemon" in path.read_text()
    assert path.stat().st_mode & 0o777 == 0o644
    assert not is_ours(tmp_path / "missing")


def test_enable_runs_reload_then_enable() -> None:
    run = FakeRun()
    assert enable(run) is None
    # restart, not --now: a reinstall after an upgrade must replace the running daemon
    assert run.calls == [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", UNIT],
        ["systemctl", "--user", "restart", UNIT],
    ]


def test_enable_reports_failure() -> None:
    assert enable(FakeRun((0, "", ""), (1, "", "Failed to enable\n"))) == "Failed to enable"
    assert enable(FakeRun((1, "", ""))) == "systemctl --user daemon-reload failed"


def test_enable_without_systemctl() -> None:
    def missing(argv: list[str]) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("systemctl")

    assert enable(missing) is not None


def test_disable_ignores_a_missing_unit() -> None:
    run = FakeRun((5, "", "Unit cardak.service not loaded."), (0, "", ""))
    assert disable(run) is None
    assert run.calls[0] == ["systemctl", "--user", "disable", "--now", UNIT]


def test_oom_policies_lists_wezterm_scopes() -> None:
    units = (
        "app-org.wezfurlong.wezterm-a.scope loaded active running WezTerm\n"
        "app-org.kde.konsole-x.scope loaded active running Konsole\n"
        "app-org.wezfurlong.wezterm-b.scope loaded active running WezTerm\n"
    )
    run = FakeRun((0, units, ""), (0, "stop\n", ""), (0, "continue\n", ""))
    assert oom_policies(run) == {
        "app-org.wezfurlong.wezterm-a.scope": "stop",
        "app-org.wezfurlong.wezterm-b.scope": "continue",
    }
    assert run.calls[1] == [
        "systemctl", "--user", "show", "-p", "OOMPolicy", "--value",
        "app-org.wezfurlong.wezterm-a.scope",
    ]  # fmt: skip


def test_oom_policies_when_systemctl_fails() -> None:
    assert oom_policies(FakeRun((1, "", "no bus"))) == {}


def test_oom_advice_text(tmp_path: Path) -> None:
    advice = oom_advice(tmp_path)
    assert f"mkdir -p {tmp_path}/{DROPIN_DIR}" in advice
    assert f"> {tmp_path}/{DROPIN_DIR}/oom.conf" in advice
    assert "printf '[Scope]\\nOOMPolicy=continue\\n'" in advice
    assert "systemctl --user daemon-reload" in advice


def test_dropin_present(tmp_path: Path) -> None:
    assert not dropin_present(tmp_path)
    d = tmp_path / DROPIN_DIR
    d.mkdir()
    (d / "x.conf").write_text("[Scope]\nOOMPolicy=stop\n")
    assert not dropin_present(tmp_path)
    (d / "oom.conf").write_text("[Scope]\nOOMPolicy = continue\n")
    assert dropin_present(tmp_path)


def test_unit_does_not_restart_while_another_daemon_runs() -> None:
    # `cardak daemon` exits 3 when another one holds the lock; restarting would loop every 5 s
    assert "RestartPreventExitStatus=3\n" in unit_text(Path("/bin/cardak"))
