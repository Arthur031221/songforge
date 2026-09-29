"""Engines turn a job into audio. Each engine runs its model in a child process."""

from __future__ import annotations

from .base import Engine, EngineError, EngineNotReady, JobResult, ProgressEvent
from .mlx_engine import MlxEngine

__all__ = [
    "Engine",
    "EngineError",
    "EngineNotReady",
    "JobResult",
    "MlxEngine",
    "ProgressEvent",
    "get_engine",
]


def get_engine(name: str, paths) -> Engine:
    if name == "mlx":
        return MlxEngine(paths)
    if name == "fake":
        from .fake import FakeEngine

        return FakeEngine(paths)
    raise ValueError(f"Unknown engine {name!r}. Choose mlx.")
