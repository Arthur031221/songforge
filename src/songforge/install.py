"""First-run setup: install the mlx-Yue engine and download the weights."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from . import config
from .engine.base import EngineError, ProgressEvent
from .engine.mlx_engine import RUNNER, MlxEngine

Echo = Callable[[str], None]


class SetupError(RuntimeError):
    pass


def gib(n: float) -> str:
    """Decimal gigabytes, the unit Hugging Face and Finder use for downloads and disk."""
    return f"{n / 1e9:.1f} GB"


def preflight(paths, covers: bool = False) -> list[str]:
    """Return blocking problems. An empty list means setup can run."""
    problems = []
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        problems.append(
            "songforge needs a Mac with Apple Silicon (M1 or later) running macOS 14.2+."
        )
    for tool, hint in (("git", "xcode-select --install"), ("uv", "brew install uv")):
        if shutil.which(tool) is None:
            problems.append(f"{tool} is not installed. Install it with: {hint}")
    need = 0 if MlxEngine(paths).status()["models"] else config.SONG_DOWNLOAD_BYTES
    if covers:
        need += config.COVER_DOWNLOAD_BYTES
    paths.home.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(paths.home).free
    if need and free < need + 2 * 2**30:
        problems.append(f"Not enough disk space: {gib(need)} to download, {gib(free)} free.")
    return problems


def engine_installed(paths) -> bool:
    head = paths.engine_dir / ".git"
    if not paths.engine_python.is_file() or not head.exists():
        return False
    proc = subprocess.run(
        ["git", "-C", str(paths.engine_dir), "rev-parse", "HEAD"], capture_output=True, text=True
    )
    return proc.stdout.strip() == config.MLX_ENGINE_COMMIT


def _run(cmd: list[str], cwd: Path | None = None, env: dict | None = None) -> None:
    proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-8:]
        raise SetupError(f"Command failed: {' '.join(cmd)}\n" + "\n".join(tail))


def install_engine(paths, echo: Echo) -> None:
    if engine_installed(paths):
        echo(f"Engine: mlx-Yue {config.MLX_ENGINE_COMMIT[:7]} already installed")
        return
    target = paths.engine_dir
    echo(f"Engine: fetching mlx-Yue {config.MLX_ENGINE_COMMIT[:7]} into {target}")
    if not (target / ".git").exists():
        target.mkdir(parents=True, exist_ok=True)
        _run(["git", "init", "-q", str(target)])
        _run(["git", "-C", str(target), "remote", "add", "origin", config.MLX_ENGINE_REPO])
    _run(
        [
            "git",
            "-C",
            str(target),
            "fetch",
            "-q",
            "--depth",
            "1",
            "origin",
            config.MLX_ENGINE_COMMIT,
        ]
    )
    _run(["git", "-C", str(target), "checkout", "-q", "--force", "FETCH_HEAD"])
    echo("Engine: creating its Python 3.12 environment with uv (MLX, no PyTorch)")
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    _run(["uv", "sync", "--frozen", "--no-dev", "--extra", "transcription"], cwd=target, env=env)


def _progress_printer(echo: Echo) -> Callable[[ProgressEvent], None]:
    state = {"label": "", "shown": -1}

    def emit(event: ProgressEvent) -> None:
        if event.label and event.label != state["label"] and not event.total:
            state["label"] = event.label
            echo(f"  {event.label}")
        if event.stage == "download" and event.total:
            pct = int(100 * min(1.0, event.done / event.total))
            if pct >= state["shown"] + 5 or pct == 100:
                state["shown"] = pct
                echo(f"  {pct:3d}%  {gib(event.done)} of {gib(event.total)}")

    return emit


def download_models(paths, echo: Echo) -> None:
    engine = MlxEngine(paths)
    if engine.status()["models"]:
        echo("Weights: already downloaded")
        return
    echo(
        f"Weights: downloading YuE2-3B (MLX) and the YuE2 VAE, about {gib(config.SONG_DOWNLOAD_BYTES)}"
    )
    job = {
        "model_repo": config.MLX_MODEL_REPO,
        "model_dir": str(paths.model_dir),
        "model_files": config.MLX_MODEL_FILES,
        "vae_repo": config.VAE_REPO,
        "vae_dir": str(paths.vae_dir),
        "vae_revision": config.VAE_REVISION,
        "expected_bytes": config.SONG_DOWNLOAD_BYTES,
    }
    _runner(paths, "download", job, echo)


def download_covers(paths, echo: Echo) -> None:
    if MlxEngine(paths).covers_ready():
        echo("Cover models: already downloaded")
        return
    echo(
        f"Cover models: downloading SheetSage2 and MERT-v2, about {gib(config.COVER_DOWNLOAD_BYTES)}"
    )
    _runner(paths, "fetch-covers", {"cache_dir": str(paths.hf_cache)}, echo)


def verify(paths, echo: Echo) -> dict:
    echo("Verify: checking the runtime and hashing the weights")
    return _runner(
        paths, "verify", {"model_dir": str(paths.model_dir), "vae_dir": str(paths.vae_dir)}, echo
    )


def _runner(paths, command: str, job: dict, echo: Echo) -> dict:
    work = paths.home / "setup"
    work.mkdir(parents=True, exist_ok=True)
    job_file = work / f"{command}.json"
    job_file.write_text(json.dumps(job))
    engine = MlxEngine(paths)
    cmd = [str(paths.engine_python), "-P", "-u", str(RUNNER), command, "--job", str(job_file)]
    try:
        return engine.run_child(cmd, _progress_printer(echo), work / f"{command}.log")
    except EngineError as error:
        raise SetupError(str(error)) from error


def run_setup(paths, echo: Echo = print, covers: bool = False) -> dict:
    paths.ensure()
    problems = preflight(paths, covers)
    if problems:
        raise SetupError("\n".join(problems))
    install_engine(paths, echo)
    download_models(paths, echo)
    if covers:
        download_covers(paths, echo)
    info = verify(paths, echo)
    echo(f"Ready: {info.get('device') or 'Apple Silicon'}, macOS {info.get('macos')}")
    return info


def disk_usage(paths) -> dict:
    def size(path: Path) -> int:
        if not path.exists():
            return 0
        return sum(p.stat().st_size for p in path.rglob("*") if p.is_file() and not p.is_symlink())

    return {
        "models": size(paths.models),
        "engine": size(paths.engine_dir),
        "songs": size(paths.songs),
        "uploads": size(paths.uploads),
    }


if __name__ == "__main__":  # pragma: no cover
    from .config import get_paths

    sys.exit(0 if run_setup(get_paths()) else 1)
