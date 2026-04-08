"""
Thread-safe SSE fan-out broadcaster.

The background tick thread calls broadcast() (sync).
Each connected SSE client has its own asyncio.Queue, populated via
loop.call_soon_threadsafe so the sync→async boundary is crossed safely.
"""
from __future__ import annotations

import asyncio
import json
import threading
from typing import Optional

_loop: Optional[asyncio.AbstractEventLoop] = None
_subscribers: list[asyncio.Queue] = []
_lock = threading.Lock()


def set_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


def subscribe() -> asyncio.Queue:
    """Register a new SSE client. Returns its dedicated queue."""
    q: asyncio.Queue = asyncio.Queue(maxsize=50)
    with _lock:
        _subscribers.append(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    with _lock:
        try:
            _subscribers.remove(q)
        except ValueError:
            pass


def broadcast(payload: dict) -> None:
    """Called from the sync tick thread. Thread-safe fan-out to all subscribers."""
    if _loop is None:
        return
    data = json.dumps(payload)
    with _lock:
        queues = list(_subscribers)
    for q in queues:
        def _put(q: asyncio.Queue = q) -> None:
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                pass  # slow client — drop; snapshot on reconnect recovers state
        _loop.call_soon_threadsafe(_put)
