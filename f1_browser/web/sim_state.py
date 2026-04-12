"""
Global sim state shared between main.py and the web layer.
The runner is only available when the simulation was started in the same process.
"""
from __future__ import annotations

import queue
import threading
from typing import Optional

_runner = None
_runner_lock = threading.RLock()
_lock = threading.Lock()
_busy = False

TICK_INTERVAL_SECONDS = 2

_tick_thread: Optional[threading.Thread] = None
_tick_running = False
_cmd_queue: queue.Queue = queue.Queue()

_TICK_SINGLE = 0
_TICK_FINISH_RACE = 1
_TICK_FINISH_SEASON = 2


def trigger_tick() -> None:
    """Wake the tick loop immediately instead of waiting for the next interval."""
    _cmd_queue.put(_TICK_SINGLE)


def trigger_finish_race() -> None:
    """Advance ticks until the current race ends (race_result payload)."""
    _cmd_queue.put(_TICK_FINISH_RACE)


def trigger_finish_season() -> None:
    """Advance ticks until the current season ends (season_standings payload)."""
    _cmd_queue.put(_TICK_FINISH_SEASON)


def is_in_race() -> bool:
    """Return True if a race lap iterator is currently active."""
    with _runner_lock:
        runner = _runner
    return runner is not None and runner.is_race_active()


def register(runner) -> None:
    with _runner_lock:
        global _runner
        _runner = runner


def get_runner():
    with _runner_lock:
        return _runner


def is_available() -> bool:
    with _runner_lock:
        return _runner is not None


def is_busy() -> bool:
    with _lock:
        return _busy


def set_busy(value: bool) -> None:
    """Allow external callers (e.g. initial batch sim) to hold the busy lock."""
    global _busy
    with _lock:
        _busy = value


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
        try:
            mode = _cmd_queue.get(timeout=TICK_INTERVAL_SECONDS)
        except queue.Empty:
            # Periodic auto-tick
            mode = _TICK_SINGLE

        if not _tick_running:
            break

        with _runner_lock:
            runner = _runner
        if runner is None:
            continue

        # Skip processing if simulate_many batch is running
        with _lock:
            currently_busy = _busy
        if currently_busy:
            continue

        while _tick_running:
            # Stop if a batch simulation took over
            with _lock:
                if _busy:
                    break
            try:
                with get_session() as db:
                    payloads = runner.tick_one_lap(db)
                for i, payload in enumerate(payloads):
                    broadcaster.broadcast(payload)
                    # Pause between season_standings and season_events so standings
                    # are visible before the off-season event list appears.
                    # Skip the pause when fast-forwarding.
                    if mode == _TICK_SINGLE and i < len(payloads) - 1:
                        try:
                            next_cmd = _cmd_queue.get(timeout=5)
                            # Re-queue so the outer loop processes it after the pause
                            _cmd_queue.put(next_cmd)
                        except queue.Empty:
                            pass

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
    with _lock:
        global _busy
        if _busy:
            return False
    with _runner_lock:
        runner = _runner
    if runner is None:
        return False

    with _lock:
        _busy = True

    def _run():
        try:
            from db.session import get_session
            for n in range(count):
                with get_session() as db:
                    runner.run_one_season(db, start_season_num + n)
        finally:
            with _lock:
                global _busy
                _busy = False

    threading.Thread(target=_run, daemon=True).start()
    return True
