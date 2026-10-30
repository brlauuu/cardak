import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

from cardak.core.limits import Stored, limits_path, load_limits
from cardak.core.statusline import PIPE_MAX, main

FIXTURES = Path(__file__).parent / "fixtures" / "statusline"
PLAN = (FIXTURES / "plan.json").read_bytes()


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    return {"XDG_STATE_HOME": str(tmp_path / "state")}


def _run(argv: list[str], data: bytes, env: dict[str, str]) -> tuple[int, list[tuple[str, bytes]]]:
    ran: list[tuple[str, bytes]] = []

    def run(command: str, given: bytes) -> int:
        ran.append((command, given))
        return 3

    return main(argv, io.BytesIO(data), env, clock=lambda: 100.0, run=run), ran


def test_records_and_runs_original_with_same_input(env: dict[str, str]) -> None:
    assert _run(["--then", "somestatus --x"], PLAN, env) == (3, [("somestatus --x", PLAN)])
    stored = load_limits(limits_path(env))
    assert isinstance(stored, Stored) and stored.read_at == 100.0
    assert stored.five_hour is not None and stored.five_hour.used == 73.0


def test_no_then_runs_nothing(env: dict[str, str]) -> None:
    assert _run([], PLAN, env) == (0, [])
    assert load_limits(limits_path(env)) is not None


def test_no_limits_writes_nothing(env: dict[str, str]) -> None:
    data = (FIXTURES / "no_limits.json").read_bytes()
    assert _run(["--then", "x"], data, env) == (3, [("x", data)])
    assert not limits_path(env).exists()


def test_record_failure_still_runs_original(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("")
    env = {"XDG_STATE_HOME": str(blocker)}  # the state dir can't be made
    assert _run(["--then", "x"], PLAN, env) == (3, [("x", PLAN)])


def test_unknown_arguments_run_nothing(env: dict[str, str]) -> None:
    assert _run(["--bogus"], PLAN, env) == (0, [])
    log = Path(env["XDG_STATE_HOME"]) / "cardak" / "hook.log"
    assert "--bogus" in log.read_text()


def _cardak(args: list[str], data: bytes, tmp_path: Path) -> subprocess.CompletedProcess[bytes]:
    script = "import sys\nfrom cardak.cli import main\nsys.exit(main())\n"
    env = os.environ | {"XDG_STATE_HOME": str(tmp_path), "CARDAK_CONFIG": str(tmp_path / "p")}
    return subprocess.run(
        [sys.executable, "-c", script, *args], input=data, env=env, capture_output=True, check=False
    )


def test_exec_relays_stdin_stdout_stderr_and_exit_code(tmp_path: Path) -> None:
    command = "cat; echo err >&2; exit 3"
    done = _cardak(["statusline", "--then", command], PLAN, tmp_path)
    assert (done.returncode, done.stdout, done.stderr) == (3, PLAN, b"err\n")
    assert (tmp_path / "cardak" / "limits.json").exists()


def test_exec_large_input_still_reaches_original(tmp_path: Path) -> None:
    data = PLAN[:-2] + b', "pad": "' + b"x" * PIPE_MAX + b'"}\n'
    done = _cardak(["statusline", "--then", "wc -c"], data, tmp_path)
    assert done.returncode == 0 and int(done.stdout) == len(data)


def test_exec_gives_original_default_signals(tmp_path: Path) -> None:
    # Python ignores SIGPIPE and SIGXFSZ; exec keeps ignored signals, so `yes | head` would print
    # "Broken pipe" in the user's status line
    done = _cardak(["statusline", "--then", "grep SigIgn /proc/self/status"], PLAN, tmp_path)
    assert int(done.stdout.split()[1], 16) & ((1 << 12) | (1 << 24)) == 0, done.stdout


def test_pipe_threshold_fits_the_smallest_pipe() -> None:
    # a user over pipe-user-pages-soft gets one-page (4 KiB) pipes; writing more would hang
    assert PIPE_MAX <= 4096
