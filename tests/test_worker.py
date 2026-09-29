import shutil
from pathlib import Path

import pytest

from songforge.db import Store
from songforge.engine.base import EngineError, JobResult, ProgressEvent
from songforge.worker import INSTRUMENTAL_LYRICS, Worker, build_job, describe, overall, public


def song_row(**kw):
    base = {
        "kind": "song",
        "title": "t",
        "style": "city pop, female vocal",
        "lyrics": "[Verse]\nla",
        "instrumental": False,
        "mode": "fast",
        "seed": 7,
        "max_seconds": 120,
    }
    return {**base, **kw}


def test_build_job_fast_and_hq():
    job = build_job(song_row())
    assert job["ode_steps"] == 8
    assert job["max_tokens"] == 120 * 25
    assert job["cot"] == "full"
    assert build_job(song_row(mode="hq"))["ode_steps"] == 32


def test_build_job_instrumental():
    job = build_job(song_row(instrumental=True, lyrics="ignored"))
    assert job["lyrics"] == INSTRUMENTAL_LYRICS
    assert job["style"].endswith("instrumental, no vocals")
    job = build_job(song_row(instrumental=True, style="lofi, instrumental"))
    assert job["style"] == "lofi, instrumental"


def test_build_job_cover_passes_source_and_score():
    job = build_job(song_row(kind="cover", source_path="/x.mp3", task="melody-vocal", abc="X:1"))
    assert job["source"] == "/x.mp3"
    assert job["task"] == "melody-vocal"
    assert job["abc"] == "X:1"


def test_overall_progress_is_monotonic_across_stages():
    values = [
        overall("song", ProgressEvent("planning", done=1, total=2)),
        overall("song", ProgressEvent("tokenizing", done=0, total=10)),
        overall("song", ProgressEvent("tokenizing", done=10, total=10)),
        overall("song", ProgressEvent("synth", done=4, total=8)),
        overall("song", ProgressEvent("render", done=1, total=1)),
    ]
    assert values == sorted(values)
    assert values[-1] <= 0.99
    assert overall("song", ProgressEvent("unknown")) == -1


def test_describe():
    assert describe(ProgressEvent("tokenizing", done=250)) == "Tokenizing: 10 s of audio written"
    assert describe(ProgressEvent("synth", done=3, total=8)) == "Synth: step 3 of 8"
    assert describe(ProgressEvent("planning", label="Loading 8bit AR model")) == (
        "Planning: Loading 8bit AR model"
    )


def test_worker_runs_fake_job(paths, fake_engine):
    store = Store(paths.db)
    worker = Worker(store, fake_engine, paths)
    row = store.create(**song_row())
    assert worker.run_once()
    done = store.get(row["id"])
    assert done["status"] == "done", done["error"]
    assert done["progress"] == 1.0
    assert done["seconds"] == 1.0
    assert done["wall_ms"] is not None
    assert done["abc"].startswith("X:1")
    assert Path(done["path"]).is_file()
    assert (paths.songs / row["id"] / "score.abc").is_file()
    assert done["peaks"] and max(done["peaks"]) == 1.0
    if shutil.which("ffmpeg"):
        assert done["path"].endswith(".flac")
        assert (paths.songs / row["id"] / "audio.mp3").is_file()
    assert not worker.run_once()


def test_worker_records_engine_errors(paths, fake_engine, monkeypatch):
    store = Store(paths.db)
    worker = Worker(store, fake_engine, paths)

    def boom(job, out, emit):
        emit(ProgressEvent("planning", "Loading"))
        raise EngineError("Out of memory budget")

    monkeypatch.setattr(fake_engine, "generate", boom)
    row = store.create(**song_row())
    worker.run_once()
    failed = store.get(row["id"])
    assert failed["status"] == "failed"
    assert "memory" in failed["error"]


def test_worker_transcribe_stores_score(paths, fake_engine):
    store = Store(paths.db)
    worker = Worker(store, fake_engine, paths)
    row = store.create(**song_row(kind="transcribe", source_path=str(paths.uploads / "a.wav")))
    worker.run_once()
    done = store.get(row["id"])
    assert done["status"] == "done"
    assert done["abc"]
    assert not done["path"]


def test_cancel_queued(paths, fake_engine):
    store = Store(paths.db)
    worker = Worker(store, fake_engine, paths)
    row = store.create(**song_row())
    assert worker.cancel(row["id"])
    assert store.get(row["id"])["status"] == "cancelled"
    assert not worker.cancel(row["id"])
    assert not worker.cancel("nope")


def test_cancel_running_calls_engine(paths, fake_engine, monkeypatch):
    store = Store(paths.db)
    worker = Worker(store, fake_engine, paths)
    row = store.create(**song_row())

    def slow(job, out, emit):
        worker.cancel(row["id"])
        fake_engine._walk(["planning"], emit)
        return JobResult()

    monkeypatch.setattr(fake_engine, "generate", slow)
    worker.run_once()
    assert store.get(row["id"])["status"] == "cancelled"


def test_public_hides_paths(paths):
    data = public({"id": "a", "path": "/x/audio.flac", "source_path": "/u/abc123.mp3"})
    assert "path" not in data and "source_path" not in data
    assert data["has_audio"] and data["has_flac"]
    assert data["upload_id"] == "abc123"


@pytest.mark.parametrize("kind", ["song", "cover"])
def test_worker_background_thread(paths, fake_engine, kind):
    import time

    store = Store(paths.db)
    worker = Worker(store, fake_engine, paths)
    worker.start()
    try:
        row = store.create(**song_row(kind=kind, source_path=str(paths.uploads / "a.wav")))
        worker.wake()
        deadline = time.time() + 10
        while time.time() < deadline and store.get(row["id"])["status"] != "done":
            time.sleep(0.05)
        assert store.get(row["id"])["status"] == "done"
    finally:
        worker.stop()
