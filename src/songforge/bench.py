"""`songforge bench`: wall time, real-time factor and peak memory for one song."""

from __future__ import annotations

import json
import platform
import time
from pathlib import Path

from . import config
from .engine.base import Engine, ProgressEvent
from .worker import build_job, describe

BENCH_STYLE = (
    "English, upbeat pop rock, bright female vocal, electric guitar, bass, punchy drums, "
    "catchy chorus, 118 BPM"
)
BENCH_LYRICS = """[Verse]
Streetlights hum a song I used to know
Every window glowing soft and low
I keep walking where the river bends
Counting all the roads that never end

[Chorus]
Hold on, hold on, the night is young
Every heartbeat is a song unsung
Turn it up and let the city ring
We were born to find a reason to sing

[Verse]
Paper tickets folded in my hand
Half a map and half a better plan
If the morning finds us far from home
We will build a place to call our own

[Chorus]
Hold on, hold on, the night is young
Every heartbeat is a song unsung
Turn it up and let the city ring
We were born to find a reason to sing

[Bridge]
When the lights go down
And the crowd goes quiet
I will hear you still
In the heart of the riot

[Chorus]
Hold on, hold on, the night is young
Every heartbeat is a song unsung
Turn it up and let the city ring
We were born to find a reason to sing
"""


def machine() -> dict:
    info = {"machine": platform.machine(), "macos": platform.mac_ver()[0] or platform.platform()}
    try:
        import subprocess

        chip = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True
        ).stdout.strip()
        mem = subprocess.run(
            ["sysctl", "-n", "hw.memsize"], capture_output=True, text=True
        ).stdout.strip()
        info["chip"] = chip
        info["memory_gb"] = round(int(mem) / 2**30) if mem else None
    except (OSError, ValueError):
        pass
    return info


def run_bench(
    paths,
    engine: Engine,
    mode: str = "fast",
    max_seconds: int = 240,
    seed: int = 42,
    style: str = BENCH_STYLE,
    lyrics: str = BENCH_LYRICS,
    echo=print,
) -> dict:
    out = paths.home / "bench" / time.strftime("%Y%m%d-%H%M%S")
    song = {
        "kind": "song",
        "style": style,
        "lyrics": lyrics,
        "instrumental": False,
        "mode": mode,
        "seed": seed,
        "max_seconds": max_seconds,
    }
    job = build_job(song)
    last = {"stage": ""}

    def emit(event: ProgressEvent) -> None:
        if event.stage != last["stage"]:
            last["stage"] = event.stage
            echo(f"  {describe(event)}")

    started = time.perf_counter()
    result = engine.generate(job, out, emit)
    wall = time.perf_counter() - started
    seconds = result.seconds or 0.0
    report = {
        "engine": engine.name,
        "model": "YuE2-3B, AR 8-bit",
        "mode": mode,
        "ode_steps": config.MODES[mode]["ode_steps"],
        "seed": seed,
        "audio_seconds": round(seconds, 2),
        "wall_seconds": round(wall, 2),
        "rtf": round(wall / seconds, 3) if seconds else None,
        "peak_rss_bytes": result.peak_rss_bytes,
        "peak_footprint_bytes": result.peak_footprint_bytes,
        "truncated": result.truncated,
        "timing": result.extra.get("timing", {}),
        "output": str(result.audio) if result.audio else None,
        "date": time.strftime("%Y-%m-%d"),
        **machine(),
    }
    (out / "bench.json").write_text(json.dumps(report, indent=2))
    return report


def fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "n/a"
    minutes, secs = divmod(round(seconds), 60)
    return f"{minutes}m {secs:02d}s" if minutes else f"{secs}s"


def fmt_bytes(n: int | None) -> str:
    return "n/a" if not n else f"{n / 2**30:.1f} GiB"


def render_table(report: dict) -> str:
    rows = [
        ("engine", f"{report['engine']} ({report['model']})"),
        ("mode", f"{report['mode']} ({report['ode_steps']} steps), seed {report['seed']}"),
        ("wall time", fmt_duration(report["wall_seconds"])),
        ("audio length", fmt_duration(report["audio_seconds"])),
        (
            "real-time factor",
            f"{report['rtf']} (wall / audio, lower is faster)"
            if report["rtf"] is not None
            else "n/a",
        ),
        ("peak RSS", fmt_bytes(report["peak_rss_bytes"])),
        ("peak footprint", fmt_bytes(report["peak_footprint_bytes"])),
        (
            "machine",
            f"{report.get('chip', '')} {report.get('memory_gb', '')} GB, "
            f"macOS {report.get('macos', '')}".strip(),
        ),
    ]
    if report.get("truncated"):
        rows.append(("note", "hit the length cap before the song ended"))
    width = max(len(k) for k, _ in rows)
    return "\n".join(f"{k.ljust(width)}  {v}" for k, v in rows)


def load_request(path: Path) -> dict:
    data = json.loads(Path(path).read_text())
    return {
        "style": data.get("style") or data.get("tags", BENCH_STYLE),
        "lyrics": data.get("lyrics", BENCH_LYRICS),
        "seed": int(data.get("seed", 42)),
    }
