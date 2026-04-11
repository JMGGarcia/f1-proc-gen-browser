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

TICK_INTERVAL_SECONDS = 2

_tick_thread: Optional[threading.Thread] = None
_tick_running = False
_tick_event = threading.Event()

_TICK_SINGLE = 0
_TICK_FINISH_RACE = 1
_TICK_FINISH_SEASON = 2
_next_mode = _TICK_SINGLE


def trigger_tick() -> None:
    """Wake the tick loop immediately instead of waiting for the next interval."""
    global _next_mode
    _next_mode = _TICK_SINGLE
    _tick_event.set()


def trigger_finish_race() -> None:
    """Advance ticks until the current race ends (race_result payload)."""
    global _next_mode
    _next_mode = _TICK_FINISH_RACE
    _tick_event.set()


def trigger_finish_season() -> None:
    """Advance ticks until the current season ends (season_standings payload)."""
    global _next_mode
    _next_mode = _TICK_FINISH_SEASON
    _tick_event.set()


def is_in_race() -> bool:
    """Return True if a race lap iterator is currently active."""
    return _runner is not None and _runner._current_lap_iter is not None


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

        global _next_mode
        mode = _next_mode
        _next_mode = _TICK_SINGLE

        while _tick_running:
            try:
                with get_session() as db:
                    payloads = _runner.tick_one_lap(db)
                for i, payload in enumerate(payloads):
                    broadcaster.broadcast(payload)
                    # Pause between season_standings and season_events so standings
                    # are visible before the off-season event list appears.
                    # Skip the pause when fast-forwarding.
                    if mode == _TICK_SINGLE and i < len(payloads) - 1:
                        _tick_event.wait(timeout=5)
                        _tick_event.clear()

                if mode == _TICK_SINGLE:
                    break
                elif mode == _TICK_FINISH_RACE:
                    if any(p.get("type") == "race_result" for p in payloads):
                        break
                elif mode == _TICK_FINISH_SEASON:
                    if any(p.get("type") in ("season_standings", "season_events") for p in payloads):
                        break
            except Exception:
                log.exception("Tick loop error")
                break


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
