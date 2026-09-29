"""The engine child-process protocol, with a stub standing in for the MLX runner."""

import json
import sys
import textwrap

import pytest

from songforge.engine import get_engine
from songforge.engine.base import Engine, EngineError, EngineNotReady, ProgressEvent
from songforge.engine.mlx_engine import REQUIRED_MODEL_FILES, MlxEngine

STUB = textwrap.dedent(
    """
    import json, sys
    def emit(**d):
        print(json.dumps(d), flush=True)
    args = sys.argv[1:]
    command = args[args.index("--job") - 1] if "--job" in args else "generate"
    job = json.load(open(args[args.index("--job") + 1]))
    print("plain log line")
    emit(event="stage", stage="planning", label="Loading 8bit AR model")
    emit(event="progress", stage="tokenizing", label="Generating song", done=500, total=6000)
    emit(event="progress", stage="synth", label="Synthesizing audio", done=8, total=8)
    if job.get("fail"):
        emit(event="error", message="Process footprint 17.00 GiB exceeds 16 GiB budget", kind="MemoryError")
        sys.exit(1)
    if job.get("crash"):
        sys.exit(3)
    if command == "cover":
        emit(event="score", abc="X:1\\nK:C\\nCDEF|")
    emit(event="result", audio=job["out"] + "/audio.flac", abc="X:1", seconds=30.0,
         wall_seconds=12.5, peak_rss_bytes=9_000_000_000, peak_footprint_bytes=10_000_000_000,
         truncated=False, timing={"e2e_seconds": 11.0}, command=command)
    """
)


def collect():
    events = []
    return events, events.append


def install_stub(paths):
    """Create the files MlxEngine looks for and a python shim that runs the stub."""
    for name in REQUIRED_MODEL_FILES:
        (paths.model_dir / name).parent.mkdir(parents=True, exist_ok=True)
        (paths.model_dir / name).write_text("x")
    paths.vae_dir.mkdir(parents=True, exist_ok=True)
    (paths.vae_dir / "model.safetensors").write_text("x")
    stub = paths.home / "stub_runner.py"
    stub.write_text(STUB)
    shim = paths.engine_python
    shim.parent.mkdir(parents=True, exist_ok=True)
    # The engine calls: <python> -P -u <runner> <command> --job <file>. Swap in the stub.
    shim.write_text(f'#!/bin/sh\nshift 3\nexec "{sys.executable}" "{stub}" "$@"\n')
    shim.chmod(0o755)


def test_run_child_translates_events(paths, tmp_path):
    engine = Engine(paths)
    job = tmp_path / "job.json"
    job.write_text(json.dumps({"out": str(tmp_path)}))
    stub = tmp_path / "stub.py"
    stub.write_text(STUB)
    events, emit = collect()
    result = engine.run_child(
        [sys.executable, str(stub), "generate", "--job", str(job)], emit, tmp_path / "log.txt"
    )
    assert result["seconds"] == 30.0
    assert [e.stage for e in events] == ["planning", "tokenizing", "synth"]
    assert events[1] == ProgressEvent("tokenizing", "Generating song", 500, 6000)
    assert "plain log line" in (tmp_path / "log.txt").read_text()


def test_run_child_error_is_friendly(paths, tmp_path):
    engine = Engine(paths)
    job = tmp_path / "job.json"
    job.write_text(json.dumps({"out": str(tmp_path), "fail": True}))
    stub = tmp_path / "stub.py"
    stub.write_text(STUB)
    with pytest.raises(EngineError, match="Out of memory budget"):
        engine.run_child([sys.executable, str(stub), "--job", str(job)], lambda e: None)


def test_run_child_crash_without_result(paths, tmp_path):
    engine = Engine(paths)
    job = tmp_path / "job.json"
    job.write_text(json.dumps({"out": str(tmp_path), "crash": True}))
    stub = tmp_path / "stub.py"
    stub.write_text(STUB)
    with pytest.raises(EngineError, match="exited with code 3"):
        engine.run_child([sys.executable, str(stub), "--job", str(job)], lambda e: None)


def test_mlx_engine_not_ready_lists_missing(paths):
    engine = MlxEngine(paths)
    st = engine.status()
    assert st["ready"] is False
    assert "ar-8bit.safetensors" in st["missing"]
    assert "yue2-vae/model.safetensors" in st["missing"]
    assert st["covers"] is False
    with pytest.raises(EngineNotReady):
        engine.generate({}, paths.songs / "x", lambda e: None)


@pytest.mark.skipif(sys.platform != "darwin", reason="the MLX engine only reports ready on macOS")
def test_mlx_engine_with_stub_runner(paths, monkeypatch):
    import platform

    monkeypatch.setattr(platform, "machine", lambda: "arm64")
    install_stub(paths)
    engine = MlxEngine(paths)
    assert engine.status()["ready"] is True
    events, emit = collect()
    out = paths.songs / "abc"
    result = engine.generate(
        {"style": "pop", "lyrics": "la", "seed": 1, "ode_steps": 8, "max_tokens": 750}, out, emit
    )
    assert result.seconds == 30.0
    assert result.peak_rss_bytes == 9_000_000_000
    assert result.extra["timing"]["e2e_seconds"] == 11.0
    job = json.loads((out / "job.json").read_text())
    assert job["precision"] == "8bit"
    assert job["model_dir"] == str(paths.model_dir)
    assert job["max_tokens"] == 750
    assert [e.stage for e in events][:3] == ["planning", "tokenizing", "synth"]


@pytest.mark.skipif(sys.platform != "darwin", reason="the MLX engine only reports ready on macOS")
def test_mlx_engine_cover_emits_score(paths, monkeypatch):
    import platform
    import shutil

    monkeypatch.setattr(platform, "machine", lambda: "arm64")
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/" + name)
    install_stub(paths)
    events, emit = collect()
    MlxEngine(paths).cover({"style": "metal", "source": "/x.mp3"}, paths.songs / "c", emit)
    scores = [e for e in events if e.stage == "score"]
    assert scores and scores[0].label.startswith("X:1")


def test_get_engine():
    from songforge.config import Paths

    p = Paths(__import__("pathlib").Path("/tmp/none"))
    assert get_engine("mlx", p).name == "mlx"
    assert get_engine("fake", p).name == "fake"
    with pytest.raises(ValueError):
        get_engine("cuda", p)
