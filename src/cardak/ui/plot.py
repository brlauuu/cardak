"""A line plot in braille characters: 2 × 4 dots per character, gaps where there is no data."""

from collections.abc import Sequence

BLANK = "⠀"
# dot bits by (column, row) inside one braille character
_DOTS = ((0x01, 0x02, 0x04, 0x40), (0x08, 0x10, 0x20, 0x80))


def _row(value: float, top: float, rows: int) -> int:
    """Dot row for a value: 0 is the top, rows - 1 the bottom."""
    level = round(min(max(value / top, 0.0), 1.0) * (rows - 1))
    return rows - 1 - level


def _columns(values: Sequence[float | None], n: int) -> list[float | None]:
    """n dot columns, each the max of its bucket of values (None when all of it is None)."""
    out: list[float | None] = []
    for c in range(n):
        start = c * len(values) // n
        end = max(start + 1, (c + 1) * len(values) // n)
        bucket = [v for v in values[start:end] if v is not None]
        out.append(max(bucket) if bucket else None)
    return out


def braille(values: Sequence[float | None], top: float, width: int, height: int) -> list[str]:
    if top <= 0 or not values:
        return [BLANK * width for _ in range(height)]
    rows = height * 4
    cells = [[0] * width for _ in range(height)]
    previous: int | None = None
    for x, value in enumerate(_columns(values, width * 2)):
        if value is None:
            previous = None  # a gap: the next point starts a new line
            continue
        y = _row(value, top, rows)
        low, high = (y, y) if previous is None else (min(y, previous), max(y, previous))
        for dot in range(low, high + 1):
            cells[dot // 4][x // 2] |= _DOTS[x % 2][dot % 4]
        previous = y
    return ["".join(chr(0x2800 + bits) for bits in line) for line in cells]


def level_row(value: float, top: float, height: int) -> int:
    """The text row a value falls on, e.g. for the threshold line."""
    return _row(value, top, height * 4) // 4
