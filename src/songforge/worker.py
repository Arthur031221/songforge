"""Single background worker. Runs one job at a time, in submission order."""

from __future__ import annotations

import logging
import shutil
import threading
import time
from pathlib import Path

from . import audio, config
from .db import Store
from .engine.base import Cancelled, Engine, EngineError, JobResult, ProgressEvent
from .events import Broker

log = logging.getLogger("songforge.worker")

# Share of the progress bar each stage owns, per job kind.
WEIGHTS = {
    "song": {"planning": 0.10, "tokenizing": 0.50, "synth": 0.32, "render": 0.08},
    "cover": {
        "transcribing": 0.20,
        "planning": 0.02,
        "tokenizing": 0.42,
        "synth": 0.28,
        "render": 0.08,
    },
    "transcribe": {"transcribing": 1.0},
}

INSTRUMENTAL_LYRICS = "[Intro]\n\n[Verse]\n\n[Chorus]\n\n[Outro]\n"


def build_job(song: dict) -> dict:
    """Translate a library row into the engine request."""
    style = song["style"].strip()
    lyrics = song["lyrics"]
    if song.get("instrumental"):
        if "instrumental" not in style.lower():
            style = f"{style}, instrumental, no vocals" if style else "instrumental, no vocals"
        lyrics = INSTRUMENTAL_LYRICS
    job = {
        "style": style,
        "lyrics": lyrics,
        "seed": int(song["seed"]),
        "ode_steps": config.MODES.get(song["mode"], config.MODES["fast"])["ode_steps"],
        "max_tokens": int(song["max_seconds"]) * config.TOKENS_PER_SECOND,
        "cot": "full",
    }
    if song["kind"] in ("cover", "transcribe"):
        job["source"] = song.get("source_path")
        job["task"] = song.get("task") or "melody-full"
        if song.get("abc"):
            job["abc"] = song["abc"]
    return job


def describe(event: ProgressEvent) -> str:
    label = config_label(event.stage)
    if event.stage == "tokenizing" and event.done:
        return f"{label}: {event.done / config.TOKENS_PER_SECOND:.0f} s of audio written"
    if event.stage == "planning" and event.done:
        return f"{label}: {int(event.done)} score tokens"
    if event.stage == "synth" and event.total:
        return f"{label}: step {int(event.done)} of {int(event.total)}"
    if event.stage in ("render", "transcribing") and event.total:
        return f"{label}: {int(event.done)} of {int(event.total)}"
    if event.label:
        return f"{label}: {event.label}"
    return label


def config_label(stage: str) -> str:
    from .engine.base import STAGE_LABELS

    return STAGE_LABELS.get(stage, stage.title())


def overall(kind: str, event: ProgressEvent) -> float:
    weights = WEIGHTS.get(kind, WEIGHTS["song"])
    if event.stage not in weights:
        return -1
    before = 0.0
    for stage, share in weights.items():
        if stage == event.stage:
            within = 0.0
            if event.total:
                within = min(1.0, float(event.done) / float(event.total))
            return round(min(0.99, before + share * within), 3)
        before += share
    return -1


class Worker:
    def __init__(self, store: Store, engine: Engine, paths, broker: Broker | None = None):
        self.store = store
        self.engine = engine
        self.paths = paths
        self.broker = broker or Broker()
        self.current: str | None = None
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # Lifecycle ---------------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="songforge-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self.current:
            self.engine.cancel()
        if self._thread:
            self._thread.join(timeout=5)

    def wake(self) -> None:
        self._wake.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                worked = self.run_once()
            except Exception:  # pragma: no cover - keep the worker alive
                log.exception("worker loop error")
                worked = False
            if not worked:
                self._wake.wait(timeout=2)
                self._wake.clear()

    # Jobs --------------------------------------------------------------------
    def cancel(self, song_id: str) -> bool:
        song = self.store.get(song_id)
        if not song:
            return False
        if song["status"] == "queued":
            self._finish(song_id, status="cancelled", error="Cancelled before it started")
            return True
        if song["status"] == "running" and self.current == song_id:
            self.engine.cancel()
            return True
        return False

    def run_once(self) -> bool:
        song = self.store.next_queued()
        if not song:
            return False
        self._process(song)
        return True

    def _publish(self, song_id: str) -> None:
        song = self.store.get(song_id)
        if song:
            self.broker.publish("song", public(song))

    def _finish(self, song_id: str, **fields) -> None:
        self.store.update(song_id, finished_at=time.time(), **fields)
        self._publish(song_id)

    def _process(self, song: dict) -> None:
        song_id = song["id"]
        out = self.paths.songs / song_id
        out.mkdir(parents=True, exist_ok=True)
        self.current = song_id
        started = time.perf_counter()
        first_stage = (
            "transcribing"
            if song["kind"] in ("cover", "transcribe") and not song.get("abc")
            else "planning"
        )
        self.store.update(
            song_id,
            status="running",
            started_at=time.time(),
            stage=first_stage,
            detail="Starting engine",
            progress=0,
            error=None,
        )
        self._publish(song_id)
        last = [0.0]

        def emit(event: ProgressEvent) -> None:
            if event.stage == "score":
                self.store.update(song_id, abc=event.label)
                self._publish(song_id)
                return
            fields = {"stage": event.stage, "detail": describe(event)}
            value = overall(song["kind"], event)
            if value >= 0:
                fields["progress"] = value
            now = time.monotonic()
            if now - last[0] < 0.25 and event.total and event.done < event.total:
                return
            last[0] = now
            self.store.update(song_id, **fields)
            self.broker.publish("progress", {"id": song_id, **fields})

        try:
            job = build_job(song)
            if song["kind"] == "transcribe":
                result = self.engine.transcribe(job, out, emit)
            elif song["kind"] == "cover":
                result = self.engine.cover(job, out, emit)
            else:
                result = self.engine.generate(job, out, emit)
            wall_ms = int((time.perf_counter() - started) * 1000)
            self._store_result(song, out, result, wall_ms)
        except Cancelled:
            self._finish(song_id, status="cancelled", error="Cancelled", stage="", detail="")
        except (EngineError, audio.AudioError, OSError, ValueError) as error:
            log.warning("job %s failed: %s", song_id, error)
            self._finish(song_id, status="failed", error=str(error), detail="")
        except Exception as error:
            log.exception("job %s crashed", song_id)
            self._finish(
                song_id, status="failed", error=f"{type(error).__name__}: {error}", detail=""
            )
        finally:
            self.current = None

    def _store_result(self, song: dict, out: Path, result: JobResult, wall_ms: int) -> None:
        song_id = song["id"]
        fields: dict = {
            "status": "done",
            "stage": "",
            "detail": "",
            "progress": 1.0,
            "wall_ms": wall_ms,
            "peak_rss": result.peak_rss_bytes,
            "abc": result.abc or song.get("abc") or "",
        }
        if fields["abc"]:
            (out / "score.abc").write_text(fields["abc"])
        if result.audio is not None:
            self.store.update(song_id, stage="render", detail="Render: encoding FLAC and MP3")
            self.broker.publish(
                "progress",
                {"id": song_id, "stage": "render", "detail": "Render: encoding FLAC and MP3"},
            )
            flac = out / "audio.flac"
            src = Path(result.audio)
            if src.suffix.lower() == ".flac":
                if src != flac:
                    shutil.copyfile(src, flac)
                final = flac
            elif audio.ffmpeg():
                final = audio.to_flac(src, flac)
            else:
                final = src
            if audio.ffmpeg():
                audio.to_mp3(final, out / "audio.mp3", title=song.get("title", ""))
            try:
                fields["peaks"] = audio.peaks(final)
            except audio.AudioError:
                fields["peaks"] = None
            fields["path"] = str(final)
            fields["seconds"] = round(result.seconds, 2)
            fields["truncated"] = int(result.truncated)
        self._finish(song_id, **fields)


def public(song: dict) -> dict:
    """Row as sent to the browser: no filesystem paths."""
    data = {k: v for k, v in song.items() if k not in ("path", "source_path")}
    data["upload_id"] = Path(song["source_path"]).stem if song.get("source_path") else None
    data["has_audio"] = bool(song.get("path"))
    data["has_mp3"] = bool(song.get("path")) and Path(song["path"]).with_suffix(".mp3").is_file()
    data["has_flac"] = bool(song.get("path")) and str(song["path"]).endswith(".flac")
    return data
