"""Paths, pinned engine sources and model manifests."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PORT = 7860

# mlx-Yue is not on PyPI. songforge installs it into its own uv environment at this commit.
MLX_ENGINE_REPO = "https://github.com/vanch007/mlx-Yue"
MLX_ENGINE_COMMIT = "9253ed133343406947bde7b67d43c7a63fb39d99"

# Song generation weights. The 8-bit AR model plans and writes codec tokens. The acoustic
# stage reuses the BF16 AR backbone for conditioning, so both AR files are required.
MLX_MODEL_REPO = "vanch007/mlx-Yue2-3B"
MLX_MODEL_FILES = [
    "ar-8bit.safetensors",
    "ar-bf16.safetensors",
    "nar-bf16.safetensors",
    "qwen.tiktoken",
    "config.json",
    "conversion.json",
    "LICENSE",
    "THIRD_PARTY_NOTICES.md",
    "README.md",
    "licenses/*",
]
VAE_REPO = "m-a-p/YuE2-Vae"
VAE_REVISION = "95535e72a97bc0f09b8ada125d26b4009428c0e8"

# Cover transcription weights, fetched on first cover (about 2.7 GB).
TRANSCRIPTION_REPO = "m-a-p/SheetSage2"
MERT_REPO = "m-a-p/MERT-v2-FullSong"

# Approximate download sizes in bytes, used for prompts and disk checks.
SONG_DOWNLOAD_BYTES = 2_656_158_264 + 4_331_951_136 + 2_929_490_456 + 530_512_720
COVER_DOWNLOAD_BYTES = 2_529_812_848 + 190_000_000

# 48 kHz audio, 1920 samples per codec frame: 25 semantic tokens per second of audio.
TOKENS_PER_SECOND = 25

MODES = {
    "fast": {"label": "Fast", "ode_steps": 8},
    "hq": {"label": "HQ", "ode_steps": 32},
}


@dataclass(frozen=True)
class Paths:
    home: Path

    @property
    def db(self) -> Path:
        return self.home / "songforge.db"

    @property
    def songs(self) -> Path:
        return self.home / "songs"

    @property
    def uploads(self) -> Path:
        return self.home / "uploads"

    @property
    def models(self) -> Path:
        return self.home / "models"

    @property
    def model_dir(self) -> Path:
        return self.models / "yue2-mlx"

    @property
    def vae_dir(self) -> Path:
        return self.models / "yue2-vae"

    @property
    def hf_cache(self) -> Path:
        return self.models / "hf-cache"

    @property
    def engine_dir(self) -> Path:
        return self.home / "engine" / "mlx-yue"

    @property
    def engine_python(self) -> Path:
        return self.engine_dir / ".venv" / "bin" / "python"

    def ensure(self) -> None:
        for path in (self.home, self.songs, self.uploads, self.models):
            path.mkdir(parents=True, exist_ok=True)


def get_paths() -> Paths:
    home = os.environ.get("SONGFORGE_HOME")
    return Paths(Path(home).expanduser() if home else Path.home() / ".songforge")


def ollama_url() -> str:
    return os.environ.get("SONGFORGE_OLLAMA_URL", "http://localhost:11434").rstrip("/")


def lyrics_model() -> str:
    return os.environ.get("SONGFORGE_LYRICS_MODEL", "qwen3:4b")
