"""How the detail views word a session: cut text, the turn's counts, paths (`i`, the web view)."""

from pathlib import Path

from cardak.core.agents import SessionState

MAX_WORDS = 100


def words(text: str, keep: str) -> str:
    """At most MAX_WORDS words, keeping the start or the end of the text."""
    split = text.split()
    if len(split) <= MAX_WORDS:
        return " ".join(split)
    if keep == "end":
        return "…" + " ".join(split[-MAX_WORDS:])
    return " ".join(split[:MAX_WORDS]) + "…"


def count(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def turn_text(state: SessionState) -> str:
    parts = [
        count(state.turn_edits, "edit", "edits") if state.turn_edits else "",
        count(state.turn_reads, "read", "reads") if state.turn_reads else "",
        count(state.turn_commands, "command", "commands") if state.turn_commands else "",
        f"{state.turn_other} other" if state.turn_other else "",
    ]
    return ", ".join(p for p in parts if p) or "nothing yet"


def shown_path(path: str, cwd: Path | None) -> str:
    p = Path(path)
    return str(p.relative_to(cwd)) if cwd and p.is_relative_to(cwd) else path
