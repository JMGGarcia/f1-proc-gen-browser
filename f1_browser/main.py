"""
F1 World Browser — entry point.

Usage:
    python main.py          # simulate then serve
    python main.py sim      # simulate only (no web server)
    python main.py serve    # web server only (no simulation)
"""
from __future__ import annotations

import os
import sys
import threading
import time

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


def run_server():
    import uvicorn
    print("Starting web server at http://127.0.0.1:8000")
    uvicorn.run("web.app:app", host="127.0.0.1", port=8000, log_level="warning")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "both"

    if mode == "sim":
        run_simulation()

    elif mode == "serve":
        run_server()

    else:
        # Run simulation in background thread, start server once DB is ready
        sim_thread = threading.Thread(target=run_simulation, daemon=True)
        sim_thread.start()

        # Give the sim a moment to init the DB before the server tries to read it
        time.sleep(1)
        run_server()
