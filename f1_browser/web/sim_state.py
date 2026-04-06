"""
Global sim state shared between main.py and the web layer.
The runner is only available when the simulation was started in the same process.
"""
from __future__ import annotations

import threading
import time
from typing import Optional

_runner = None
_lock = threading.Lock()
_busy = False

TICK_INTERVAL_SECONDS = 10

_tick_thread: Optional[threading.Thread] = None
_tick_running = False
_tick_event = threading.Event()


def trigger_tick() -> None:
    """Wake the tick loop immediately instead of waiting for the next interval."""
    _tick_event.set()


def register(runner) -> None:
    global _runner
    _runner = runner


def get_runner():
    return _runner


def is_available() -> bool:
    return _runner is not None


def is_busy() -> bool:
    return _busy


def start_tick_loop() -> None:
    """Start the background tick loop. Called once at server startup."""
    global _tick_thread, _tick_running
    if _tick_thread is not None:
        return
    _tick_running = True
    _tick_thread = threading.Thread(target=_tick_loop, daemon=True, name="tick-loop")
    _tick_thread.start()


def stop_tick_loop() -> None:
    global _tick_running
    _tick_running = False


def is_tick_running() -> bool:
    return _tick_running


def _tick_loop() -> None:
    import logging
    from db.session import get_session
    from web import broadcaster
    log = logging.getLogger(__name__)
    while _tick_running:
        _tick_event.wait(timeout=TICK_INTERVAL_SECONDS)
        _tick_event.clear()
        if not _tick_running:
            break
        if _runner is None:
            continue
        try:
            with get_session() as db:
                payloads = _runner.tick_one_race(db)
            for i, payload in enumerate(payloads):
                broadcaster.broadcast(payload)
                # Pause between season_standings and season_events so standings
                # are visible before the off-season event list appears.
                # Use the tick event so a keypress can skip the wait.
                if i < len(payloads) - 1:
                    _tick_event.wait(timeout=5)
                    _tick_event.clear()
        except Exception:
            log.exception("Tick loop error")


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
