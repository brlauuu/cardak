from cardak.ui.plot import BLANK, braille, level_row

LEFT = 0x01 | 0x02 | 0x04 | 0x40
RIGHT = 0x08 | 0x10 | 0x20 | 0x80


def bits(c: str) -> int:
    return ord(c) - ord(BLANK)


def test_braille_flat_line() -> None:
    [line] = braille([5.0, 5.0, 5.0, 5.0], 10.0, 2, 1)
    assert len(line) == 2 and line[0] == line[1] != BLANK
    assert bits(line[0]) == 0x02 | 0x10  # row 1 of 4, both columns


def test_braille_gap() -> None:
    [line] = braille([1.0, None, None, 1.0], 10.0, 2, 1)
    assert bits(line[0]) & RIGHT == 0 and bits(line[0]) & LEFT
    assert bits(line[1]) & LEFT == 0 and bits(line[1]) & RIGHT


def test_braille_vertical_fill() -> None:
    [line] = braille([0.0, 10.0], 10.0, 1, 1)
    assert bits(line[0]) == 0x40 | RIGHT  # bottom-left dot, then a filled climb on the right


def test_braille_rows_and_buckets() -> None:
    lines = braille([0.0, 10.0] * 50, 10.0, 4, 2)  # 100 values into 8 dot columns: max per bucket
    assert len(lines) == 2 and all(len(x) == 4 for x in lines)
    assert all(bits(c) & (0x01 | 0x08) for c in lines[0])  # every bucket reaches the top
    assert braille([], 10.0, 3, 1) == [BLANK * 3]
    assert braille([1.0], 0.0, 1, 1) == [BLANK]


def test_level_row() -> None:
    assert level_row(10.0, 10.0, 3) == 0
    assert level_row(5.0, 10.0, 3) == 1
    assert level_row(0.0, 10.0, 3) == 2
    assert level_row(99.0, 10.0, 3) == 0
