"""Write, enable and remove `cardak.service`; check OOMPolicy. Imports nothing from cardak.

Measured on systemd 255: WezTerm windows run in `app-org.wezfurlong.wezterm-<id>.scope`, whose
OOMPolicy defaults to `stop`, so one OOM kill closes the whole window. A prefix drop-in in
`app-org.wezfurlong.wezterm-.scope.d/` with `OOMPolicy=continue` keeps it open.
"""

import os
import re
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path

MARKER = "# cardak: generated"
UNIT = "cardak.service"
SCOPE_PREFIX = "app-org.wezfurlong.wezterm-"
DROPIN_DIR = "app-org.wezfurlong.wezterm-.scope.d"

Run = Callable[[list[str]], subprocess.CompletedProcess[str]]


def run_systemctl(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, check=False, timeout=30)


def unit_dir(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    base = env.get("XDG_CONFIG_HOME") or str(Path(env.get("HOME", "~")).expanduser() / ".config")
    return Path(base) / "systemd" / "user"


def unit_text(cardak_bin: Path) -> str:
    return (
        f"{MARKER} — `cardak resources uninstall` removes it\n"
        "[Unit]\n"
        "Description=cardak: resource alerts for WezTerm panes\n"
        "\n"
        "[Service]\n"
        f"ExecStart={cardak_bin} daemon\n"
        "Restart=on-failure\n"
        "RestartSec=5\n"
        "RestartPreventExitStatus=3\n"  # another daemon holds the lock
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def is_ours(path: Path) -> bool:
    try:
        with path.open(encoding="utf-8") as f:
            return f.readline().startswith(MARKER)
    except OSError:
        return False


def write_unit(path: Path, text: str, force: bool = False) -> None:
    """Atomic, mode 0644; refuses a unit cardak didn't write unless force. Raises OSError."""
    if path.exists() and not force and not is_ours(path):
        raise FileExistsError(f"{path} was not written by cardak (use --force to replace it)")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _systemctl(run: Run, *args: str) -> subprocess.CompletedProcess[str] | str:
    """The result, or an error text when systemctl is missing or failed to start."""
    try:
        return run(["systemctl", "--user", *args])
    except (OSError, subprocess.SubprocessError) as e:
        return f"systemctl --user {' '.join(args)}: {e}"


def _failed(result: subprocess.CompletedProcess[str] | str) -> str | None:
    if isinstance(result, str):
        return result
    if result.returncode == 0:
        return None
    return result.stderr.strip() or f"{' '.join(result.args)} failed"


def enable(run: Run = run_systemctl) -> str | None:
    """daemon-reload, enable, restart (a reinstall replaces a running daemon); the error or None."""
    for args in (("daemon-reload",), ("enable", UNIT), ("restart", UNIT)):
        if error := _failed(_systemctl(run, *args)):
            return error
    return None


def disable(run: Run = run_systemctl) -> str | None:
    result = _systemctl(run, "disable", "--now", UNIT)
    error = _failed(result)
    if error and "not loaded" not in error and "does not exist" not in error:
        return error
    return _failed(_systemctl(run, "daemon-reload"))


def oom_policies(run: Run = run_systemctl) -> dict[str, str]:
    """Running WezTerm window scopes → their OOMPolicy; {} when systemctl can't tell."""
    listing = _systemctl(run, "list-units", "--type=scope", "--plain", "--no-legend")
    if _failed(listing) or isinstance(listing, str):
        return {}
    scopes = [line.split()[0] for line in listing.stdout.splitlines() if line.strip()]
    policies: dict[str, str] = {}
    for scope in (s for s in scopes if s.startswith(SCOPE_PREFIX)):
        shown = _systemctl(run, "show", "-p", "OOMPolicy", "--value", scope)
        if not isinstance(shown, str) and shown.returncode == 0:
            policies[scope] = shown.stdout.strip()
    return policies


def dropin_present(root: Path) -> bool:
    """A drop-in under root that sets OOMPolicy=continue for WezTerm windows."""
    pattern = re.compile(r"^\s*OOMPolicy\s*=\s*continue\s*$", re.MULTILINE)
    try:
        confs = sorted((root / DROPIN_DIR).glob("*.conf"))
        return any(pattern.search(p.read_text(encoding="utf-8")) for p in confs)
    except OSError:
        return False


def oom_advice(dropin_root: Path) -> str:
    d = dropin_root / DROPIN_DIR
    return (
        "When memory runs out, the kernel kills one process, but systemd then closes the whole\n"
        "WezTerm window it ran in (OOMPolicy=stop). To keep the window and lose only that "
        "process:\n"
        "\n"
        f"  mkdir -p {d}\n"
        f"  printf '[Scope]\\nOOMPolicy=continue\\n' > {d}/oom.conf\n"
        "  systemctl --user daemon-reload\n"
        "\n"
        "WezTerm windows opened after that keep running when one of their processes is killed.\n"
    )
