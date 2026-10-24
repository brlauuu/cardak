"""The only place that talks to WezTerm: socket discovery, `wezterm cli list`, parsing.

Always `--no-auto-start`: without it, a call with no GUI running spawns a headless
mux server. Outside a pane, `wezterm cli` only tries the mux socket, so the GUI's
`gui-sock-<pid>` is found here and passed in WEZTERM_UNIX_SOCKET.
"""

import json
import os
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class Pane:
    pane_id: int
    window_id: int
    tab_id: int
    workspace: str
    title: str
    cwd: Path | None
    tty: str | None
    is_active: bool


class WeztermUnavailable(Exception):
    pass


def parse_cwd(url: str | None) -> Path | None:
    if not url:
        return None
    parts = urlsplit(url)
    if parts.scheme != "file":
        return None
    path = unquote(parts.path)  # the host part (OSC 7 hostname) is ignored
    return Path(path) if path else None


def parse_panes(text: str) -> list[Pane]:
    return [
        Pane(
            pane_id=int(p["pane_id"]),
            window_id=int(p["window_id"]),
            tab_id=int(p["tab_id"]),
            workspace=str(p.get("workspace", "")),
            title=str(p.get("title", "")),
            cwd=parse_cwd(p.get("cwd")),
            tty=p.get("tty_name") or None,
            is_active=bool(p.get("is_active", False)),
        )
        for p in json.loads(text)
    ]


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _live_gui_socks(directory: Path) -> dict[int, Path]:
    """pid → socket, for every `gui-sock-<pid>` whose GUI process still runs."""
    out: dict[int, Path] = {}
    for sock in directory.glob("gui-sock-*"):
        pid = sock.name.removeprefix("gui-sock-")
        if pid.isdigit() and _pid_alive(int(pid)):
            out[int(pid)] = sock
    return out


def _scope(pid: int, proc: Path) -> str | None:
    try:
        text = (proc / str(pid) / "cgroup").read_text()
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("0::"):
            name = line.rsplit("/", 1)[-1]
            return name if name.endswith(".scope") else None
    return None


def gui_processes(
    env: Mapping[str, str] | None = None, proc: Path = Path("/proc")
) -> dict[int, str | None]:
    """Every running WezTerm GUI: pid → its systemd scope, or None when not in one."""
    env = os.environ if env is None else env
    runtime = env.get("XDG_RUNTIME_DIR")
    if not runtime:
        return {}
    return {pid: _scope(pid, proc) for pid in _live_gui_socks(Path(runtime) / "wezterm")}


def find_socket(env: Mapping[str, str] | None = None) -> Path | None:
    env = os.environ if env is None else env
    explicit = env.get("WEZTERM_UNIX_SOCKET")
    if explicit and Path(explicit).exists():
        return Path(explicit)
    runtime = env.get("XDG_RUNTIME_DIR")
    if not runtime:
        return None
    directory = Path(runtime) / "wezterm"
    live = list(_live_gui_socks(directory).values())
    if live:
        return max(live, key=lambda sock: sock.stat().st_mtime)
    mux = directory / "sock"
    return mux if mux.exists() else None


def _run_cli(args: list[str], env: Mapping[str, str], runner: Runner) -> str:
    """Run `wezterm cli --no-auto-start <args>` against the GUI socket; return stdout."""
    sock = find_socket(env)
    if sock is None:
        raise WeztermUnavailable("no WezTerm GUI socket found")
    try:
        out = runner(
            ["wezterm", "cli", "--no-auto-start", *args],
            capture_output=True,
            text=True,
            timeout=3,
            env={**env, "WEZTERM_UNIX_SOCKET": str(sock)},
        )
    except FileNotFoundError:
        raise WeztermUnavailable("wezterm is not installed") from None
    except subprocess.TimeoutExpired:
        raise WeztermUnavailable("wezterm cli timed out") from None
    if out.returncode != 0:
        lines = out.stderr.strip().splitlines()
        raise WeztermUnavailable(
            lines[-1] if lines else f"wezterm cli exited with {out.returncode}"
        )
    return out.stdout


def list_panes(env: Mapping[str, str] | None = None, runner: Runner = subprocess.run) -> list[Pane]:
    env = os.environ if env is None else env
    stdout = _run_cli(["list", "--format", "json"], env, runner)
    try:
        return parse_panes(stdout)
    except (ValueError, KeyError, TypeError) as e:
        raise WeztermUnavailable(f"unexpected wezterm cli output: {e}") from None


def activate_pane(
    pane_id: int, env: Mapping[str, str] | None = None, runner: Runner = subprocess.run
) -> None:
    """Select the pane and its tab. Does not raise the window (measured in M0)."""
    env = os.environ if env is None else env
    _run_cli(["activate-pane", "--pane-id", str(pane_id)], env, runner)
