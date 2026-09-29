"""Test engine. Walks through the real stage names and writes a short tone.

Select it with SONGFORGE_ENGINE=fake or --engine fake. It needs no weights, which
lets the test suite and UI work run on any machine.
"""

from __future__ import annotations

import math
import os
import struct
import time
import wave
from pathlib import Path

from .base import Emit, Engine, JobResult, ProgressEvent

FAKE_ABC = """X:1
T:songforge test tone
M:4/4
L:1/8
Q:1/4=100
K:C
V:1
"C"E2G2 c2G2|"F"A2c2 f2c2|"G"B2d2 g2d2|"C"c8|
"""


class FakeEngine(Engine):
    name = "fake"

    def __init__(self, paths, seconds: float = 4.0, delay: float | None = None):
        super().__init__(paths)
        self.seconds = seconds
        self.delay = float(os.environ.get("SONGFORGE_FAKE_DELAY", "0")) if delay is None else delay

    def status(self) -> dict:
        return {
            "engine": self.name,
            "apple_silicon": True,
            "installed": True,
            "models": True,
            "missing": [],
            "covers": True,
            "ffmpeg": True,
            "ready": True,
        }

    def _walk(self, stages: list[str], emit: Emit) -> None:
        for stage in stages:
            if self._cancel.is_set():
                from .base import Cancelled

                raise Cancelled("Cancelled")
            emit(ProgressEvent(stage, stage.title()))
            for i in range(1, 5):
                emit(ProgressEvent(stage, stage.title(), i, 4))
                if self.delay:
                    time.sleep(self.delay / 4)

    def generate(self, job: dict, out: Path, emit: Emit) -> JobResult:
        self._cancel.clear()
        self._walk(["planning", "tokenizing", "synth", "render"], emit)
        return self._result(job, out, FAKE_ABC)

    def cover(self, job: dict, out: Path, emit: Emit) -> JobResult:
        self._cancel.clear()
        stages = ["planning", "tokenizing", "synth", "render"]
        if not job.get("abc"):
            stages.insert(0, "transcribing")
        self._walk(stages, emit)
        return self._result(job, out, job.get("abc") or FAKE_ABC)

    def transcribe(self, job: dict, out: Path, emit: Emit) -> JobResult:
        self._cancel.clear()
        self._walk(["transcribing"], emit)
        return JobResult(abc=FAKE_ABC)

    def _result(self, job: dict, out: Path, abc: str) -> JobResult:
        out.mkdir(parents=True, exist_ok=True)
        path = out / "engine-audio.wav"
        write_tone(path, self.seconds, seed=int(job.get("seed", 0)))
        return JobResult(
            audio=path, abc=abc, seconds=self.seconds, wall_seconds=0.01, peak_rss_bytes=50_000_000
        )


def write_tone(path: Path, seconds: float, rate: int = 16000, seed: int = 0) -> None:
    """Write a stereo arpeggio so players and waveforms have something real to show."""
    notes = [261.63, 329.63, 392.0, 523.25]
    shift = 1 + (seed % 5) * 0.05
    frames = int(seconds * rate)
    step = max(1, frames // 8)
    data = bytearray()
    for n in range(frames):
        freq = notes[(n // step) % len(notes)] * shift
        env = 1 - (n % step) / step
        value = int(12000 * env * math.sin(2 * math.pi * freq * n / rate))
        data += struct.pack("<hh", value, value)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(data))
