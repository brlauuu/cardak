"""The latest plan limits across sessions (#70), and how the deck shows them.

`cardak statusline` records each session's reading in `limits.json`. A session keeps handing over
the figure from its last reply, so an idle one repeats an older, lower use: readings are merged per
window, never just overwritten. Use only grows within a window, so the same `resets_at` keeps the
higher figure and a later `resets_at` is a new window.
"""

import fcntl
import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from cardak.collectors.limits import Reading, Window
from cardak.core.config import state_dir

MIN_WRITE_S = 60  # an unchanged reading rewrites the file at most this often
STALE_S = 900  # older than this, the row says how old it is
LOW_LEFT = 20  # percent left: below this the bar turns yellow...
CRITICAL_LEFT = 10  # ...and red below this

Level = Literal["ok", "low", "critical", "unknown"]


@dataclass(frozen=True)
class Stored:
    read_at: float  # the last reading at least as new as the stored one
    five_hour: Window | None
    seven_day: Window | None


def limits_path(env: Mapping[str, str] | None = None) -> Path:
    return state_dir(env) / "limits.json"


def _merge_window(old: Window | None, new: Window | None) -> tuple[Window | None, bool]:
    """The window to keep, and whether `new` was at least as new as `old`."""
    if new is None:
        return old, False
    if old is None or new.resets_at > old.resets_at:
        return new, True
    if new.resets_at < old.resets_at:
        return old, False
    return (new if new.used > old.used else old), new.used >= old.used


def merge(old: Stored | None, new: Reading, now: float) -> Stored:
    five, fresh_five = _merge_window(old.five_hour if old else None, new.five_hour)
    week, fresh_week = _merge_window(old.seven_day if old else None, new.seven_day)
    if old is None or fresh_five or fresh_week:
        return Stored(now, five, week)
    return Stored(old.read_at, five, week)


def needs_write(old: Stored | None, merged: Stored, now: float) -> bool:
    if old is None or (old.five_hour, old.seven_day) != (merged.five_hour, merged.seven_day):
        return True
    return merged.read_at == now and now - old.read_at >= MIN_WRITE_S


def _window_json(w: Window | None) -> dict[str, float] | None:
    return None if w is None else {"used": w.used, "resets_at": w.resets_at}


def _window_from(raw: object) -> Window | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError(raw)
    used, resets = raw.get("used"), raw.get("resets_at")
    if not all(isinstance(v, int | float) and not isinstance(v, bool) for v in (used, resets)):
        raise ValueError(raw)
    return Window(float(used), float(resets))  # type: ignore[arg-type]


def load_limits(path: Path) -> Stored | None:
    """The stored limits; None when there are none or the file isn't cardak's shape."""
    try:
        raw = json.loads(path.read_bytes())
        read_at = raw["read_at"]
        if isinstance(read_at, bool) or not isinstance(read_at, int | float):
            return None
        five, week = _window_from(raw["five_hour"]), _window_from(raw["seven_day"])
        return Stored(float(read_at), five, week)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _save(path: Path, stored: Stored) -> None:
    text = json.dumps(
        {
            "read_at": stored.read_at,
            "five_hour": _window_json(stored.five_hour),
            "seven_day": _window_json(stored.seven_day),
        }
    )
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def record(path: Path, reading: Reading, now: float) -> None:
    """Merge one reading into the file; the lock keeps two sessions' refreshes from racing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        old = load_limits(path)
        merged = merge(old, reading, now)
        if needs_write(old, merged, now):
            _save(path, merged)


@dataclass(frozen=True)
class LimitView:
    left: int | None  # percent left; None: unknown (no reading, or the window has reset)
    resets_at: float | None
    level: Level


@dataclass(frozen=True)
class LimitsView:
    five_hour: LimitView
    seven_day: LimitView
    age_s: float  # since the reading


def _view(w: Window | None, now: float) -> LimitView:
    if w is None or w.resets_at <= now:
        return LimitView(None, None, "unknown")
    left = round(100 - w.used)
    level: Level = "critical" if left < CRITICAL_LEFT else "low" if left < LOW_LEFT else "ok"
    return LimitView(left, w.resets_at, level)


def limits_view(stored: Stored, now: float) -> LimitsView:
    five, week = _view(stored.five_hour, now), _view(stored.seven_day, now)
    return LimitsView(five, week, now - stored.read_at)
