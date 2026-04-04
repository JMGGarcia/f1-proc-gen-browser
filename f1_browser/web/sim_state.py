"""
Global sim state shared between main.py and the web layer.
The runner is only available when the simulation was started in the same process.
"""
from __future__ import annotations

import threading
from typing import Optional

_runner = None
_lock = threading.Lock()
_busy = False


def register(runner) -> None:
    global _runner
    _runner = runner


def get_runner():
    return _runner


def is_available() -> bool:
    return _runner is not None


def is_busy() -> bool:
    return _busy


def simulate_many(start_season_num: int, count: int) -> bool:
    """Spawn a background thread to run `count` seasons. Returns False if already running."""
    global _busy
    with _lock:
        if _busy or _runner is None:
            return False
        _busy = True

    def _run():
        global _busy
        try:
            from db.session import get_session
            for n in range(count):
                with get_session() as db:
                    _runner.run_one_season(db, start_season_num + n)
        finally:
            _busy = False

    threading.Thread(target=_run, daemon=True).start()
    return True
