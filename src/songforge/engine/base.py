"""Engine interface shared by the worker, the CLI and the tests."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

STAGES = ["transcribing", "planning", "tokenizing", "synth", "render"]
STAGE_LABELS = {
    "download": "Download",
    "verify": "Verify",
    "transcribing": "Transcribing",
    "planning": "Planning",
    "tokenizing": "Tokenizing",
    "synth": "Synth",
    "render": "Render",
}


class EngineError(RuntimeError):
    """The engine process failed. The message is safe to show in the UI."""


class EngineNotReady(EngineError):
    """The engine or its weights are not installed yet."""


class Cancelled(EngineError):
    pass


@dataclass
class ProgressEvent:
    stage: str
    label: str = ""
    done: float = 0
    total: float | None = None


@dataclass
class JobResult:
    audio: Path | None = None
    abc: str = ""
    seconds: float = 0.0
    wall_seconds: float = 0.0
    peak_rss_bytes: int | None = None
    peak_footprint_bytes: int | None = None
    truncated: bool = False
    extra: dict = field(default_factory=dict)


Emit = Callable[[ProgressEvent], None]


class Engine:
    """Base engine. Subclasses implement readiness checks and the job commands."""

    name = "base"

    def __init__(self, paths):
        self.paths = paths
        self._proc: subprocess.Popen | None = None
        self._cancel = threading.Event()

    # Readiness -------------------------------------------------------------
    def status(self) -> dict:
        raise NotImplementedError

    def ready(self) -> bool:
        return bool(self.status().get("ready"))

    # Jobs ------------------------------------------------------------------
    def generate(self, job: dict, out: Path, emit: Emit) -> JobResult:
        raise NotImplementedError

    def cover(self, job: dict, out: Path, emit: Emit) -> JobResult:
        raise NotImplementedError

    def transcribe(self, job: dict, out: Path, emit: Emit) -> JobResult:
        raise NotImplementedError

    def cancel(self) -> None:
        self._cancel.set()
        proc = self._proc
        if proc and proc.poll() is None:
            proc.terminate()

    # Child process protocol --------------------------------------------------
    def run_child(self, command: list[str], emit: Emit, log_path: Path | None = None) -> dict:
        """Run a runner process and translate its JSON lines into progress events."""
        self._cancel.clear()
        log = open(log_path, "a") if log_path else None  # noqa: SIM115 - closed in finally
        result: dict | None = None
        error: dict | None = None
        try:
            self._proc = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=log or subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
            assert self._proc.stdout is not None
            for line in self._proc.stdout:
                line = line.strip()
                if not line.startswith("{"):
                    if log:
                        log.write(line + "\n")
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                kind = msg.get("event")
                if kind == "stage":
                    emit(ProgressEvent(msg.get("stage", ""), msg.get("label", "")))
                elif kind == "progress":
                    emit(
                        ProgressEvent(
                            msg.get("stage", ""),
                            msg.get("label", ""),
                            msg.get("done", 0),
                            msg.get("total"),
                        )
                    )
                elif kind == "score":
                    emit(ProgressEvent("score", msg.get("abc", "")))
                elif kind == "result":
                    result = msg
                elif kind == "error":
                    error = msg
            code = self._proc.wait()
        finally:
            self._proc = None
            if log:
                log.close()
        if self._cancel.is_set():
            raise Cancelled("Cancelled")
        if error:
            raise EngineError(friendly_error(error.get("message", ""), error.get("kind", "")))
        if code != 0 or result is None:
            hint = f" See {log_path}" if log_path else ""
            raise EngineError(f"The engine exited with code {code}.{hint}")
        return result


def friendly_error(message: str, kind: str = "") -> str:
    if "exceeds" in message and "GiB" in message:
        return f"Out of memory budget: {message}. Close other apps or pick a shorter song."
    if "ffmpeg" in message.lower() and "not found" in message.lower():
        return "ffmpeg is required for covers. Install it with: brew install ffmpeg"
    return message or kind or "Unknown engine error"


def python_cmd() -> str:
    return sys.executable
