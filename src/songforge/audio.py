"""ffmpeg helpers: FLAC and MP3 export, waveform peaks, duration."""

from __future__ import annotations

import array
import shutil
import subprocess
import wave
from pathlib import Path

PEAK_RATE = 4000


class AudioError(RuntimeError):
    pass


def ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


def _run(args: list[str]) -> bytes:
    exe = ffmpeg()
    if exe is None:
        raise AudioError("ffmpeg is not installed. Install it with: brew install ffmpeg")
    proc = subprocess.run([exe, "-v", "error", "-nostdin", "-y", *args], capture_output=True)
    if proc.returncode != 0:
        raise AudioError(proc.stderr.decode(errors="replace").strip() or "ffmpeg failed")
    return proc.stdout


def to_flac(src: Path, dst: Path) -> Path:
    _run(["-i", str(src), "-c:a", "flac", str(dst)])
    return dst


def to_mp3(src: Path, dst: Path, bitrate: str = "192k", title: str = "") -> Path:
    args = ["-i", str(src), "-vn", "-c:a", "libmp3lame", "-b:a", bitrate]
    if title:
        args += ["-metadata", f"title={title}"]
    _run([*args, str(dst)])
    return dst


def decode_mono(src: Path, rate: int = PEAK_RATE) -> array.array:
    """Return signed 16-bit mono samples. Uses the wave module for WAV if ffmpeg is missing."""
    if ffmpeg() is None and src.suffix.lower() == ".wav":
        with wave.open(str(src), "rb") as w:
            raw = w.readframes(w.getnframes())
            channels = w.getnchannels()
        samples = array.array("h", raw)
        return array.array("h", samples[::channels])
    raw = _run(["-i", str(src), "-ac", "1", "-ar", str(rate), "-f", "s16le", "pipe:1"])
    return array.array("h", raw)


def peaks(src: Path, buckets: int = 480) -> list[float]:
    samples = decode_mono(src)
    if not samples:
        return []
    size = max(1, len(samples) // buckets)
    out = []
    for start in range(0, len(samples), size):
        chunk = samples[start : start + size]
        out.append(round(max(abs(min(chunk)), abs(max(chunk))) / 32768, 3))
    top = max(out) or 1.0
    return [round(v / top, 3) for v in out[:buckets]]


def duration(src: Path) -> float:
    if src.suffix.lower() == ".wav":
        with wave.open(str(src), "rb") as w:
            return w.getnframes() / w.getframerate()
    samples = decode_mono(src, rate=PEAK_RATE)
    return len(samples) / PEAK_RATE
