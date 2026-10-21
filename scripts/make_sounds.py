"""Generate cardak's bundled notification sounds into src/cardak/sounds/.

Pure synthesis (stdlib only), so the pack carries no third-party audio. Re-run after
changing a voice: `uv run scripts/make_sounds.py`.
"""

import math
import struct
import wave
from pathlib import Path

RATE = 44_100
OUT = Path(__file__).resolve().parent.parent / "src" / "cardak" / "sounds"

# name: (partials as (frequency Hz, amplitude), start offsets in s per note, duration s, decay)
Voice = tuple[list[tuple[float, float]], list[tuple[float, float]], float, float]
VOICES: dict[str, Voice] = {
    # two rising notes, soft sine with a faint octave
    "chime": ([(1.0, 1.0), (2.0, 0.25)], [(0.0, 659.25), (0.12, 987.77)], 0.75, 6.0),
    # short plucked string: bright partials, fast decay
    "pluck": ([(1.0, 1.0), (2.0, 0.5), (3.0, 0.3), (4.0, 0.15)], [(0.0, 440.0)], 0.5, 9.0),
    # bell: inharmonic partials, long tail
    "bell": ([(1.0, 1.0), (2.76, 0.4), (5.4, 0.2)], [(0.0, 880.0)], 0.95, 4.5),
    # a single short blip
    "blip": ([(1.0, 1.0)], [(0.0, 1046.5)], 0.18, 18.0),
    # two falling notes
    "drop": ([(1.0, 1.0), (2.0, 0.2)], [(0.0, 783.99), (0.1, 523.25)], 0.6, 7.0),
}

ATTACK = 0.008  # seconds; avoids a click at the start
PEAK = 0.45  # of full scale; notifications should be soft


def render(voice: Voice) -> list[float]:
    partials, notes, duration, decay = voice
    n = int(RATE * duration)
    samples = [0.0] * n
    for start, freq in notes:
        first = int(start * RATE)
        for i in range(first, n):
            t = (i - first) / RATE
            env = min(1.0, t / ATTACK) * math.exp(-decay * t)
            samples[i] += env * sum(
                amp * math.sin(2 * math.pi * freq * mult * t) for mult, amp in partials
            )
    # fade the last 20 ms so the file ends at silence
    fade = int(0.02 * RATE)
    for i in range(fade):
        samples[n - fade + i] *= 1 - i / fade
    peak = max(abs(s) for s in samples) or 1.0
    return [s / peak * PEAK for s in samples]


def write(path: Path, samples: list[float]) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"".join(struct.pack("<h", int(s * 32767)) for s in samples))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, voice in VOICES.items():
        write(OUT / f"{name}.wav", render(voice))
        print(f"wrote {OUT / name}.wav")


if __name__ == "__main__":
    main()
