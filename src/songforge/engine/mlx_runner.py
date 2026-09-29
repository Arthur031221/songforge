"""Job runner that executes inside the mlx-Yue environment.

songforge starts this file with the engine's own Python. It must only import the
standard library, numpy and lyra (mlx-Yue). It reads one job file and writes JSON
lines to stdout:

    {"event": "stage", "stage": "tokenizing", "label": "Generating song"}
    {"event": "progress", "stage": "tokenizing", "done": 250, "total": 6000}
    {"event": "result", ...}
    {"event": "error", "message": "..."}
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import resource
import shutil
import sys
import threading
import time
import traceback
from pathlib import Path

_OUT = sys.stdout
_LOCK = threading.Lock()


def emit(event: str, **data) -> None:
    with _LOCK:
        _OUT.write(json.dumps({"event": event, **data}) + "\n")
        _OUT.flush()


def classify(label: str) -> str | None:
    text = label.lower()
    if "planning" in text or "provided score" in text or "ar model" in text:
        return "planning"
    if "verifying" in text:
        return "planning"
    if "generating song" in text:
        return "tokenizing"
    if "synthesizing" in text or "acoustic" in text:
        return "synth"
    if "decod" in text:
        return "render"
    return None


class Reporter:
    """Stands in for the upstream progress stage and forwards counts as JSON."""

    def __init__(self, stage: str, label: str, total: int | None):
        self.stage, self.label, self.total = stage, label, total
        self.done = 0
        self._last = 0.0

    def _send(self, force: bool = False) -> None:
        now = time.monotonic()
        if force or now - self._last >= 0.5:
            self._last = now
            emit("progress", stage=self.stage, label=self.label, done=self.done, total=self.total)

    def update(self, completed, total=None):
        self.done = int(completed)
        if total is not None:
            self.total = int(total)
        self._send(force=self.total is not None and self.done >= self.total)

    def advance(self, count=1):
        self.done += int(count)
        self._send()

    def token(self, phase, token):
        self.advance()

    def finish(self, status="completed"):
        self._send(force=True)


def pipeline_class(caps: dict):
    from lyra.pipeline import YuE2Pipeline

    class Pipeline(YuE2Pipeline):
        stage = "planning"

        @contextlib.contextmanager
        def _status(self, label, *, total=None, unit=None):
            stage = classify(label) or Pipeline.stage
            Pipeline.stage = stage
            if total is None and stage in caps:
                total = caps[stage]
            emit("stage", stage=stage, label=label)
            reporter = Reporter(stage, label, total)
            yield reporter
            reporter.finish()

    return Pipeline


GIB, MIB = 2**30, 2**20
STAMP = ".songforge-verified.json"


def file_stamp(model_dir: Path) -> dict:
    return {
        p.name: [p.stat().st_size, p.stat().st_mtime_ns]
        for p in sorted(Path(model_dir).iterdir())
        if p.is_file() and p.name != STAMP
    }


def stamp_path(model_dir: Path) -> Path:
    # Written next to the model folder: the converted-model check forbids extra files inside it.
    return Path(model_dir).parent / f"{Path(model_dir).name}{STAMP}"


def skip_rehash_when_verified(model_dir: Path) -> bool:
    """Reuse a full hash check from setup while file sizes and mtimes are unchanged.

    Upstream hashes all 10 GB of weights on every load, about 30 s per song. The
    structural checks (manifest, file list, shapes) still run on every load.
    """
    stamp = stamp_path(model_dir)
    try:
        saved = json.loads(stamp.read_text())
    except (OSError, ValueError):
        return False
    if saved != file_stamp(model_dir):
        return False
    from lyra import conversion, pipeline

    def quick_verify(directory):
        path = Path(directory).expanduser().resolve()
        manifest = conversion._read_json(path / "conversion.json", "conversion manifest")
        return conversion._validate_conversion(path, manifest, verify_hashes=False)

    pipeline.verify_conversion = quick_verify
    return True


def relax_memory_guard() -> None:
    """Keep the per-process budget, stop only on sustained critical memory.

    Upstream stops at the first non-normal pressure sample and at 64 MiB of new
    system-wide swap-out. On a laptop with a browser and other apps open, other
    processes trip both checks long before this job is at risk, and a song that
    stops halfway is worse than one that finishes slowly. songforge keeps the
    process footprint budget and stops when macOS reports critical pressure, or
    less than 512 MiB available, for 10 seconds in a row.
    SONGFORGE_STRICT_MEMORY=1 restores the upstream check.
    """
    if os.environ.get("SONGFORGE_STRICT_MEMORY") == "1":
        return
    from lyra import measure

    sustained = 40  # samples, 0.25 s apart

    def check(self, sample):
        footprint = sample["physical_footprint_bytes"]
        if footprint > self.memory_budget_gib * GIB:
            raise MemoryError(
                f"Process footprint {footprint / GIB:.2f} GiB exceeds "
                f"{self.memory_budget_gib:g} GiB budget"
            )
        low = (
            sample["system_memory_pressure_level"] >= 4
            or sample["system_available_bytes"] < 512 * MIB
        )
        self._songforge_low = (getattr(self, "_songforge_low", 0) + 1) if low else 0
        if self._songforge_low >= sustained:
            raise MemoryError(
                "macOS reported critical memory pressure for 10 seconds. "
                "Close other apps or quit local model servers, then try again."
            )

    measure.GPUExecution._check_sample = check


def peak_memory() -> dict:
    data = {"peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
    with contextlib.suppress(Exception):
        from lyra.measure import memory_snapshot

        snap = memory_snapshot()
        data["peak_footprint_bytes"] = snap.get("process_lifetime_peak_footprint_bytes")
        data["mlx_peak_bytes"] = snap.get("mlx_peak_bytes")
    return data


def render(job: dict, out: Path, abc: str | None = None) -> dict:
    from yue2.protocol import GenerationConfig

    max_tokens = int(job.get("max_tokens", 6000))
    steps = int(job.get("ode_steps", 8))
    request = {
        "style": job["style"],
        "lyrics": job.get("lyrics", ""),
        "seed": int(job.get("seed", 0)),
        "cot": job.get("cot", "full"),
        "id": "song",
    }
    abc = abc if abc is not None else job.get("abc")
    if abc:
        request["abc"] = abc
    config = GenerationConfig(ode_steps=steps)
    skip_rehash_when_verified(Path(job["model_dir"]))
    Pipeline = pipeline_class({"tokenizing": max_tokens})
    started = time.perf_counter()
    with Pipeline(
        job["model_dir"],
        job["vae_dir"],
        precision=job.get("precision", "8bit"),
        generation_config=config,
        memory_budget_gib=float(job.get("memory_budget_gib", 16)),
        progress=True,
    ) as pipe:
        result = pipe(**request, semantic_sampling={"max_tokens": max_tokens})
        emit("stage", stage="render", label="Writing audio")
        artifacts = out / "engine"
        result.save_artifacts(artifacts)
    wall = time.perf_counter() - started
    score = artifacts / "score.abc"
    return {
        "audio": str(artifacts / "audio.flac"),
        "abc": score.read_text() if score.is_file() else (abc or ""),
        "seconds": len(result.audio) / result.sample_rate,
        "truncated": bool(result.truncated.get("semantic") or result.truncated.get("abc")),
        "engine_seconds": wall,
        "timing": result.timing,
    }


def transcribe(job: dict, out: Path) -> dict:
    from lyra.transcription.pipeline import transcribe as run

    emit("stage", stage="transcribing", label="Loading transcription models")

    def progress(info):
        if info.get("stage") == "encoding":
            emit(
                "progress",
                stage="transcribing",
                label="Listening",
                done=info.get("window", 0),
                total=info.get("windows"),
            )
        elif info.get("stage") == "decoding":
            emit(
                "progress",
                stage="transcribing",
                label="Writing score",
                done=info.get("tokens", 0),
                total=None,
            )

    from lyra.measure import GPUExecution

    def cancelled():
        guard.check()
        return False

    with GPUExecution(memory_budget_gib=float(job.get("memory_budget_gib", 16))) as guard:
        result = run(
            job["source"],
            out / "transcription",
            cache_dir=job.get("cache_dir"),
            offline=bool(job.get("covers_cached")),
            task=job.get("task", "melody-full"),
            progress=progress,
            cancelled=cancelled,
        )
        guard.check()
    import gc

    import mlx.core as mx

    gc.collect()
    mx.clear_cache()
    if result.get("status") != "complete" or result.get("truncated"):
        raise RuntimeError("Transcription did not produce a complete score")
    abc = (out / "transcription" / "score.abc").read_text()
    return {"abc": abc}


def download(job: dict) -> dict:
    from huggingface_hub import snapshot_download

    targets = [
        (job["model_repo"], job["model_dir"], job.get("model_files"), None),
        (job["vae_repo"], job["vae_dir"], None, job.get("vae_revision")),
    ]
    expected = int(job.get("expected_bytes", 0))
    roots = [Path(t[1]) for t in targets]
    stop = threading.Event()

    def watch():
        while not stop.wait(2):
            total = 0
            for root in roots:
                if root.exists():
                    total += sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
            emit(
                "progress",
                stage="download",
                label="Downloading weights",
                done=total,
                total=expected or None,
            )

    thread = threading.Thread(target=watch, daemon=True)
    thread.start()
    try:
        for repo, local_dir, patterns, revision in targets:
            emit("stage", stage="download", label=f"Downloading {repo}")
            snapshot_download(
                repo_id=repo, local_dir=local_dir, allow_patterns=patterns, revision=revision
            )
            # The converted-model check rejects anything outside its manifest.
            shutil.rmtree(Path(local_dir) / ".cache", ignore_errors=True)
    finally:
        stop.set()
    return {"model_dir": job["model_dir"], "vae_dir": job["vae_dir"]}


def fetch_covers(job: dict) -> dict:
    from lyra.transcription.model import resolve_models

    emit("stage", stage="download", label="Downloading SheetSage2 and MERT-v2")
    paths = resolve_models(offline=False, cache_dir=job.get("cache_dir"))
    return {"paths": [str(p) for p in paths]}


def verify(job: dict) -> dict:
    from lyra.conversion import verify_conversion
    from lyra.runtime import require_supported_runtime
    from yue2.storage import model_identity

    emit("stage", stage="verify", label="Checking runtime")
    runtime = require_supported_runtime()
    emit("stage", stage="verify", label="Hashing model files")
    verify_conversion(job["model_dir"])
    model_identity(Path(job["vae_dir"]))
    stamp_path(Path(job["model_dir"])).write_text(json.dumps(file_stamp(Path(job["model_dir"]))))
    return {
        "device": runtime.get("device", {}).get("device_name", ""),
        "macos": runtime.get("macos"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="songforge engine runner (mlx-Yue)")
    parser.add_argument(
        "command",
        choices=("generate", "cover", "transcribe", "download", "fetch-covers", "verify"),
    )
    parser.add_argument("--job", required=True, type=Path)
    args = parser.parse_args(argv)
    job = json.loads(args.job.read_text())
    out = Path(job.get("out", args.job.parent))
    os.environ.setdefault("MLX_ENABLE_TF32", "0")
    started = time.perf_counter()
    try:
        if args.command in ("generate", "cover", "transcribe"):
            relax_memory_guard()
        if args.command == "generate":
            data = render(job, out)
        elif args.command == "cover":
            abc = job.get("abc")
            if not abc:
                abc = transcribe(job, out)["abc"]
                emit("score", abc=abc)
            data = render({**job, "cot": "melody"}, out, abc=abc)
        elif args.command == "transcribe":
            data = transcribe(job, out)
        elif args.command == "download":
            data = download(job)
        elif args.command == "fetch-covers":
            data = fetch_covers(job)
        else:
            data = verify(job)
    except BaseException as error:
        emit(
            "error",
            message=str(error) or type(error).__name__,
            kind=type(error).__name__,
            trace=traceback.format_exc(limit=6),
        )
        return 1
    emit("result", wall_seconds=time.perf_counter() - started, **data, **peak_memory())
    return 0


if __name__ == "__main__":
    sys.exit(main())
