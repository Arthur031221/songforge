"""Cross-process engine lock. One YuE2 process at a time, even across the studio and the CLI."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path


class EngineLock:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._fd: int | None = None

    def try_acquire(self) -> bool:
        if self._fd is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
        self._fd = fd
        return True

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None


def engine_busy(path: Path) -> bool:
    """True when another process is running an engine job right now."""
    lock = EngineLock(path)
    if lock.try_acquire():
        lock.release()
        return False
    return True
