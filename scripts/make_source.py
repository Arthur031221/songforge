"""Render a public-domain melody to WAV, to use as a cover source.

Jingle Bells (James Lord Pierpont, 1857) and Auld Lang Syne (traditional) are in
the public domain. This renders a plain piano-and-bass arrangement so the demo
cover never depends on someone else's recording.

    python scripts/make_source.py jingle-bells out.wav
"""

from __future__ import annotations

import array
import math
import sys
import wave

RATE = 22050
NOTE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}

# (note, beats). Octave digits follow scientific pitch notation. "r" is a rest.
JINGLE_VERSE = (
    "G4 1 E5 1 D5 1 C5 1 G4 3 G4 .5 G4 .5 G4 1 E5 1 D5 1 C5 1 A4 4 "
    "A4 1 F5 1 E5 1 D5 1 B4 4 G5 1 G5 1 F5 1 D5 1 E5 4 "
    "G4 1 E5 1 D5 1 C5 1 G4 4 G4 1 E5 1 D5 1 C5 1 A4 3 A4 1 "
    "A4 1 F5 1 E5 1 D5 1 G5 1 G5 1 G5 1 G5 1 A5 1 G5 1 F5 1 D5 1 C5 2 G5 2"
)
JINGLE_CHORUS = (
    "E5 1 E5 1 E5 2 E5 1 E5 1 E5 2 E5 1 G5 1 C5 1.5 D5 .5 E5 4 "
    "F5 1 F5 1 F5 1.5 F5 .5 F5 1 E5 1 E5 1 E5 .5 E5 .5 E5 1 D5 1 D5 1 E5 1 D5 2 G5 2 "
    "E5 1 E5 1 E5 2 E5 1 E5 1 E5 2 E5 1 G5 1 C5 1.5 D5 .5 E5 4 "
    "F5 1 F5 1 F5 1.5 F5 .5 F5 1 E5 1 E5 1 E5 .5 E5 .5 G5 1 G5 1 F5 1 D5 1 C5 4"
)
# One chord root per bar (4 beats), matching the melody above.
JINGLE_BASS = "C C F G C F G C C C F G C F G C C C C C F C D G C C C C F C G C"

AULD = (
    "C5 1 F5 1.5 F5 .5 F5 1 A5 1 G5 1.5 F5 .5 G5 1 A5 .5 G5 .5 F5 1.5 F5 .5 A5 1 C6 1 D6 3 "
    "D6 1 C6 1.5 A5 .5 A5 1 F5 1 G5 1.5 F5 .5 G5 1 A5 .5 G5 .5 F5 1.5 D5 .5 D5 1 C5 1 F5 3 "
    "D6 1 C6 1.5 A5 .5 A5 1 F5 1 G5 1.5 F5 .5 G5 1 D6 1 C6 1.5 A5 .5 A5 1 C6 1 D6 3 "
    "F6 1 C6 1.5 A5 .5 A5 1 F5 1 G5 1.5 F5 .5 G5 1 A5 .5 G5 .5 F5 1.5 D5 .5 D5 1 C5 1 F5 3"
)
AULD_BASS = "F F C C F F Bb Bb F F C C D D Bb F F F C C F F Bb Bb F F C C D D Bb F"

SONGS = {
    "jingle-bells": {
        "melody": JINGLE_VERSE + " " + JINGLE_CHORUS,
        "bass": JINGLE_BASS,
        "bpm": 132,
        "pickup": 0,
    },
    "auld-lang-syne": {"melody": AULD, "bass": AULD_BASS, "bpm": 84, "pickup": 1},
}


def freq(name: str) -> float:
    letter, rest = name[0], name[1:]
    shift = 0
    while rest and rest[0] in "#b":
        shift += 1 if rest[0] == "#" else -1
        rest = rest[1:]
    octave = int(rest) if rest else 2
    midi = 12 * (octave + 1) + NOTE[letter] + shift
    return 440.0 * 2 ** ((midi - 69) / 12)


def parse(melody: str) -> list[tuple[str, float]]:
    tokens = melody.split()
    return [(tokens[i], float(tokens[i + 1])) for i in range(0, len(tokens), 2)]


def piano(buf: array.array, start: int, dur: float, f: float, amp: float) -> None:
    n = int(dur * RATE)
    for i in range(min(n + RATE // 3, len(buf) - start)):
        t = i / RATE
        env = math.exp(-3.2 * t) * (1 if i < n else math.exp(-12 * (i - n) / RATE))
        s = (
            math.sin(2 * math.pi * f * t)
            + 0.45 * math.sin(4 * math.pi * f * t)
            + 0.18 * math.sin(6 * math.pi * f * t)
        )
        buf[start + i] += amp * env * s * min(1.0, i / 60)


def render(song: str, path: str) -> float:
    spec = SONGS[song]
    beat = 60 / spec["bpm"]
    notes = parse(spec["melody"])
    total_beats = sum(b for _, b in notes) + spec["pickup"]
    length = int((total_beats * beat + 2) * RATE)
    buf = array.array("d", [0.0]) * length
    pos = spec["pickup"] * beat
    for name, beats in notes:
        if name != "r":
            piano(buf, int(pos * RATE), beats * beat * 0.95, freq(name), 0.34)
        pos += beats * beat
    bar = 4 * beat
    for i, root in enumerate(spec["bass"].split()):
        t0 = spec["pickup"] * beat + i * bar
        if t0 * RATE >= length:
            break
        base = freq(root + "2")
        for k in range(2):
            piano(buf, int((t0 + k * 2 * beat) * RATE), 1.8 * beat, base, 0.28)
        for semis in (0, 4, 7):
            piano(
                buf, int((t0 + beat) * RATE), 0.9 * beat, freq(root + "3") * 2 ** (semis / 12), 0.07
            )
            piano(
                buf,
                int((t0 + 3 * beat) * RATE),
                0.9 * beat,
                freq(root + "3") * 2 ** (semis / 12),
                0.07,
            )
    peak = max(abs(v) for v in buf) or 1.0
    pcm = array.array("h", (int(v / peak * 26000) for v in buf))
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    return length / RATE


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in SONGS:
        print(__doc__)
        print("songs:", ", ".join(SONGS))
        sys.exit(2)
    seconds = render(sys.argv[1], sys.argv[2])
    print(f"wrote {sys.argv[2]} ({seconds:.1f} s)")
