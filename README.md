# F1 World Browser

A procedurally generated Formula 1 world simulation with a browser-based wiki interface. Seasons simulate autonomously — drivers age and retire, teams develop their cars, engines evolve, and a live event feed tracks it all.

## Requirements

- Python 3.12+
- pip / virtualenv

## Setup

```bash
cd f1_browser
python3.12 -m venv .venv
source .venv/bin/activate
pip install fastapi uvicorn sqlalchemy jinja2
```

## Running

### First run (simulate + serve)

```bash
python main.py
```

This seeds the world, simulates the configured number of seasons (default 50), then starts the web server at [http://127.0.0.1:8000](http://127.0.0.1:8000).

### Subsequent runs (load existing DB + serve)

```bash
python main.py
```

If a database already exists it will skip the initial simulation, reconstruct the world state from the DB, and start the server with the **Simulate Season** button active.

### Simulate only (no web server)

```bash
python main.py sim
```

### Web server only (no simulation)

```bash
python main.py serve
```

## Simulating additional seasons

With the server running, click the **▶ Simulate Season N** button on the home page. Each click runs one season in the background; the page will show "Season in progress" while it runs.

## Configuration

Edit `sim/constants.py` to tune the simulation:

| Constant | Default | Description |
|---|---|---|
| `NUMBER_OF_SEASONS` | 50 | Seasons to simulate on first run |
| `MAX_TEAMS_PER_ENGINE` | 3 | Max teams a single engine supplier can service |
| `DRIVERS_POOL` | 40 | Total drivers in the driver pool |
| `REVOLUTION_PROBABILITY` | 0.2 | Chance of a formula regulation reset per season |
| `HISTORY_YEARS` | 5 | Seasons a team principal's record is judged over |

## Project layout

```
f1_browser/
├── sim/           # Simulation engine (race logic, driver aging, team AI)
├── db/            # SQLAlchemy models and session management
├── web/           # FastAPI app, routes, Jinja2 templates
├── names/         # First/last name lists per nationality
├── main.py        # Entry point
└── f1_world.db    # SQLite database (created on first run)
```

## Nationalities

Drivers are generated from 17 nationalities: 🇵🇹 PT · 🇬🇧 EN · 🇫🇷 FR · 🇩🇪 GE · 🇪🇸 ES · 🇦🇺 AU · 🇮🇹 IT · 🇯🇵 JP · 🇷🇺 RU · 🇧🇷 BR · 🇳🇱 NL · 🇫🇮 FI · 🇲🇽 MX · 🇺🇸 US · 🇮🇳 IN · 🇦🇷 AR · 🇸🇪 SE

To add a new nationality, create `names/<CODE>/first.txt` and `names/<CODE>/last.txt` (one name per line), then add the flag emoji to `sim/flags.py`.
