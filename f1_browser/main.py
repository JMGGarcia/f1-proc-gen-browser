"""
F1 World Browser — entry point.

Usage:
    python main.py                    # simulate then serve
    python main.py sim                # simulate only (no web server)
    python main.py serve              # web server only (no simulation)
    python main.py restore            # list available backups
    python main.py restore <season>   # restore DB from a backup (season number or filename)
"""
from __future__ import annotations

import os
import shutil
import sys
import threading
import time
from pathlib import Path

# Run from the project root so relative imports and template paths work
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from db.session import get_session, init_db
from sim.constants import SimulationConstants
from sim.loader import load_world_from_db
from sim.seeder import seed_world
from sim.world import WorldRunner
from web import sim_state

NAMES_DIR = "./names"
N_SEASONS = SimulationConstants.NUMBER_OF_SEASONS


def _start_keyboard_listener() -> None:
    """Listen for keypresses and trigger a tick immediately. No-op if stdin is not a TTY."""
    import select
    import sys

    if not sys.stdin.isatty():
        return

    try:
        import termios
        import tty
    except ImportError:
        return  # Windows — skip

    def _listen() -> None:
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            print("  SPACE: tick  |  K: finish race  |  S: finish season", flush=True)
            while True:
                ready, _, _ = select.select([sys.stdin], [], [], 1.0)
                if ready:
                    ch = sys.stdin.read(1)
                    if ch == " ":
                        sim_state.trigger_tick()
                    elif ch in ("k", "K"):
                        if sim_state.is_in_race():
                            sim_state.trigger_finish_race()
                        else:
                            sim_state.trigger_tick()
                    elif ch in ("s", "S"):
                        sim_state.trigger_finish_season()
        except Exception:
            pass
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

    threading.Thread(target=_listen, daemon=True, name="keyboard-listener").start()


def run_simulation():
    print("Initialising database...")
    init_db()

    with get_session() as db:
        # Check if world already has data
        from db.models import Season
        existing = db.query(Season).count()
        if existing > 0:
            print(f"Database contains {existing} seasons. Reconstructing world state...")
            tracks, engines, teams, drivers, driver_gen, sponsors = load_world_from_db(db, names_dir=NAMES_DIR)
            runner = WorldRunner(
                tracks=tracks,
                engines=engines,
                teams=teams,
                drivers=drivers,
                driver_generator=driver_gen,
                n_seasons=0,
                sponsors=sponsors,
            )
            sim_state.register(runner)
            print("World state reconstructed — simulate button is active.")
            return

    print("Seeding initial world state...")
    with get_session() as db:
        tracks, engines, teams, drivers, driver_gen, sponsors = seed_world(db, names_dir=NAMES_DIR)

    print(f"Running {N_SEASONS} seasons...")
    runner = WorldRunner(
        tracks=tracks,
        engines=engines,
        teams=teams,
        drivers=drivers,
        driver_generator=driver_gen,
        n_seasons=N_SEASONS,
        sponsors=sponsors,
    )
    sim_state.register(runner)

    with get_session() as db:
        runner.run(db)

    print(f"Simulation complete — {N_SEASONS} seasons written to database.")


def _load_world_into_runner():
    """Load world state from DB and register the runner (for serve-only mode).
    Skipped if a runner is already registered (e.g. in 'both' mode where
    run_simulation() already called sim_state.register())."""
    if sim_state.is_available():
        return
    from db.models import Season
    with get_session() as db:
        existing = db.query(Season).count()
        if existing == 0:
            print("No simulation data found. Run 'python main.py sim' first.")
            return
        print(f"Loading world state from {existing} seasons...")
        tracks, engines, teams, drivers, driver_gen, sponsors = load_world_from_db(db, names_dir=NAMES_DIR)
    runner = WorldRunner(
        tracks=tracks,
        engines=engines,
        teams=teams,
        drivers=drivers,
        driver_generator=driver_gen,
        n_seasons=0,
        sponsors=sponsors,
    )
    sim_state.register(runner)
    print("World state loaded — tick loop is active.")


def run_server():
    import uvicorn
    init_db()  # ensure all tables exist (safe to call even if DB already has data)
    _load_world_into_runner()
    _start_keyboard_listener()
    print("Starting web server at http://127.0.0.1:8000")
    uvicorn.run("web.app:app", host="127.0.0.1", port=8000, log_level="warning")


def run_restore(arg: str | None):
    from db.backup import BACKUP_DIR, DB_PATH, list_backups

    backups = list_backups(BACKUP_DIR)

    if arg is None:
        if not backups:
            print("No backups found in backups/")
            return
        print("Available backups:")
        current_world = None
        for world_id8, season_num, path in backups:
            if world_id8 != current_world:
                print(f"\n  World {world_id8}:")
                current_world = world_id8
            print(f"    Season {season_num:4d}  ({path.name})")
        print()
        print("Usage: python main.py restore <season_number_or_filename>")
        return

    # Resolve argument to a backup path
    backup_path: Path | None = None
    if arg.endswith(".db"):
        candidate = BACKUP_DIR / arg
        if candidate.exists():
            backup_path = candidate
    else:
        try:
            target_season = int(arg)
        except ValueError:
            print(f"Error: '{arg}' is not a valid season number or filename.")
            sys.exit(1)
        matches = [(w, s, p) for w, s, p in backups if s == target_season]
        if len(matches) == 1:
            backup_path = matches[0][2]
        elif len(matches) > 1:
            print(f"Multiple backups found for season {target_season}:")
            for w, s, p in matches:
                print(f"  {p.name}  (world {w})")
            print("Please specify the exact filename.")
            sys.exit(1)

    if backup_path is None:
        print(f"Error: no backup found for '{arg}'.")
        sys.exit(1)

    print(f"WARNING: This will overwrite {DB_PATH} with:")
    print(f"  {backup_path.name}")
    answer = input("Type 'yes' to confirm: ").strip()
    if answer != "yes":
        print("Aborted.")
        return

    shutil.copy2(backup_path, DB_PATH)
    for stale in (Path(f"{DB_PATH}-wal"), Path(f"{DB_PATH}-shm")):
        if stale.exists():
            stale.unlink()

    print(f"Restored from {backup_path.name}.")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "both"

    if mode == "sim":
        run_simulation()

    elif mode == "serve":
        run_server()

    elif mode == "restore":
        run_restore(sys.argv[2] if len(sys.argv) > 2 else None)

    else:
        # Run simulation in background thread, start server once DB is ready
        sim_thread = threading.Thread(target=run_simulation, daemon=True)
        sim_thread.start()

        # Give the sim a moment to init the DB before the server tries to read it
        time.sleep(1)
        run_server()
