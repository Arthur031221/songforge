"""In-process pub/sub that feeds the server-sent events stream."""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading


class Broker:
    """The worker thread publishes, each SSE client owns one asyncio queue."""

    def __init__(self) -> None:
        self._subs: set[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = set()
        self._lock = threading.Lock()

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        with self._lock:
            self._subs.add((asyncio.get_running_loop(), queue))
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._subs = {s for s in self._subs if s[1] is not queue}

    def publish(self, event: str, data: dict) -> None:
        message = (event, data)
        with self._lock:
            subs = list(self._subs)
        for loop, queue in subs:
            try:
                loop.call_soon_threadsafe(_offer, queue, message)
            except RuntimeError:
                # The client's loop is closed. Drop it.
                self.unsubscribe(queue)

    @property
    def subscribers(self) -> int:
        with self._lock:
            return len(self._subs)


def _offer(queue: asyncio.Queue, message) -> None:
    if queue.full():
        with contextlib.suppress(asyncio.QueueEmpty):
            queue.get_nowait()
    queue.put_nowait(message)


def format_sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"
