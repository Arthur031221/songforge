"""mlx-Yue engine: YuE2-3B on Apple Silicon through MLX, one child process per job."""

from __future__ import annotations

import json
import platform
import shutil
from pathlib import Path

from .. import config
from .base import Emit, Engine, EngineNotReady, JobResult

RUNNER = Path(__file__).with_name("mlx_runner.py")
REQUIRED_MODEL_FILES = [
    "ar-8bit.safetensors",
    "ar-bf16.safetensors",
    "nar-bf16.safetensors",
    "qwen.tiktoken",
    "config.json",
    "conversion.json",
]


class MlxEngine(Engine):
    name = "mlx"

    def status(self) -> dict:
        p = self.paths
        missing = [f for f in REQUIRED_MODEL_FILES if not (p.model_dir / f).is_file()]
        if not (p.vae_dir / "model.safetensors").is_file():
            missing.append("yue2-vae/model.safetensors")
        apple = platform.system() == "Darwin" and platform.machine() == "arm64"
        installed = p.engine_python.is_file()
        return {
            "engine": self.name,
            "apple_silicon": apple,
            "installed": installed,
            "models": not missing,
            "missing": missing,
            "covers": self.covers_ready(),
            "ffmpeg": shutil.which("ffmpeg") is not None,
            "ready": apple and installed and not missing,
        }

    def covers_ready(self) -> bool:
        cache = self.paths.hf_cache
        return all(
            any(
                (cache / f"models--{repo.replace('/', '--')}" / "snapshots").glob(
                    "*/model.safetensors"
                )
            )
            for repo in (config.TRANSCRIPTION_REPO, config.MERT_REPO)
        )

    def _require_ready(self) -> None:
        st = self.status()
        if not st["apple_silicon"]:
            raise EngineNotReady("The MLX engine needs a Mac with Apple Silicon (M1 or later).")
        if not st["installed"]:
            raise EngineNotReady("The engine is not installed. Run: songforge setup")
        if st["missing"]:
            raise EngineNotReady(
                "Model files are missing: " + ", ".join(st["missing"]) + ". Run: songforge setup"
            )

    def _job_file(self, out: Path, payload: dict) -> Path:
        out.mkdir(parents=True, exist_ok=True)
        payload = {
            "model_dir": str(self.paths.model_dir),
            "vae_dir": str(self.paths.vae_dir),
            "cache_dir": str(self.paths.hf_cache),
            "precision": "8bit",
            "out": str(out),
            **payload,
        }
        path = out / "job.json"
        path.write_text(json.dumps(payload, indent=2))
        return path

    def _command(self, name: str, job_file: Path) -> list[str]:
        return [
            str(self.paths.engine_python),
            "-P",
            "-u",
            str(RUNNER),
            name,
            "--job",
            str(job_file),
        ]

    def _run(self, name: str, job: dict, out: Path, emit: Emit) -> JobResult:
        self._require_ready()
        if name in ("cover", "transcribe") and not shutil.which("ffmpeg"):
            raise EngineNotReady("Covers need ffmpeg. Install it with: brew install ffmpeg")
        job_file = self._job_file(out, job)
        data = self.run_child(self._command(name, job_file), emit, out / "engine.log")
        return JobResult(
            audio=Path(data["audio"]) if data.get("audio") else None,
            abc=data.get("abc", ""),
            seconds=float(data.get("seconds", 0.0)),
            wall_seconds=float(data.get("wall_seconds", 0.0)),
            peak_rss_bytes=data.get("peak_rss_bytes"),
            peak_footprint_bytes=data.get("peak_footprint_bytes"),
            truncated=bool(data.get("truncated")),
            extra={"timing": data.get("timing", {})},
        )

    def generate(self, job: dict, out: Path, emit: Emit) -> JobResult:
        return self._run("generate", job, out, emit)

    def cover(self, job: dict, out: Path, emit: Emit) -> JobResult:
        return self._run("cover", job, out, emit)

    def transcribe(self, job: dict, out: Path, emit: Emit) -> JobResult:
        return self._run("transcribe", job, out, emit)
