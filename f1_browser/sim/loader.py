"""
Reconstruct in-memory sim objects from the existing database state so the
WorldRunner can continue simulating seasons beyond the initial run.

Caveats (acceptable approximations):
- Direction development/scouting stats are approximated from stored direction_avg.
- Driver/engine contract years default to 3 (mid-range value).
- Direction position_history is rebuilt from the last HISTORY_YEARS TeamSeasonStats.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from db import models as m
from sim.constants import SimulationConstants
from sim.drivers import Driver, DriverGenerator
from sim.teams import Direction, Engine, Team
from sim.tracks import Track


def load_world_from_db(db: Session, names_dir: str = "./names"):
    # ── Tracks ──────────────────────────────────────────────────────────
    db_tracks = db.query(m.Track).order_by(m.Track.id).all()
    tracks = [
        Track(
            name=t.name,
            downforce_over_engine=t.downforce_over_engine,
            car_over_driver=t.car_over_driver,
            db_id=t.id,
        )
        for t in db_tracks
    ]

    # ── Latest completed season ──────────────────────────────────────────
    latest_season = db.query(m.Season).filter_by(completed=True).order_by(m.Season.number.desc()).first()
    if latest_season is None:
        raise ValueError("No completed seasons in database — cannot reconstruct world.")
    latest_season_id = latest_season.id
    latest_season_num = latest_season.number

    # ── Engines ─────────────────────────────────────────────────────────
    db_engines = db.query(m.Engine).order_by(m.Engine.id).all()
    engines = []
    engine_map: dict[int, Engine] = {}
    for e in db_engines:
        eng = Engine(
            name=e.name,
            power=e.power,
            reliability=e.reliability,
            color_primary=e.color_primary,
            color_secondary=e.color_secondary,
            db_id=e.id,
        )
        engines.append(eng)
        engine_map[e.id] = eng

    # ── Active drivers ───────────────────────────────────────────────────
    db_drivers = db.query(m.Driver).filter_by(retired=False).all()
    drivers: list[Driver] = []
    driver_map: dict[int, Driver] = {}
    for d in db_drivers:
        # Get latest season stats for skill/age
        stats = (
            db.query(m.DriverSeasonStats)
            .filter_by(driver_id=d.id)
            .order_by(m.DriverSeasonStats.season_id.desc())
            .first()
        )
        skill = stats.skill if stats else 0.3
        age = stats.age if stats else 25
        top_skill = stats.top_skill if stats else skill

        drv = Driver(
            db_id=d.id,
            first_name=d.first_name,
            last_name=d.last_name,
            nationality=d.nationality,
            skill=skill,
            age=age,
        )
        drv.base_skill = skill
        drv.top_skill = top_skill
        drivers.append(drv)
        driver_map[d.id] = drv

    # ── Teams ────────────────────────────────────────────────────────────
    db_teams = db.query(m.Team).order_by(m.Team.id).all()
    teams: list[Team] = []
    for t in db_teams:
        ts = (
            db.query(m.TeamSeasonStats)
            .filter_by(team_id=t.id, season_id=latest_season_id)
            .first()
        )
        chassis = ts.chassis if ts else 0.5

        # Reconstruct direction
        direction = _rebuild_direction(db, t.id, ts, latest_season_num)

        # Find current engine (from latest TeamSeasonStats)
        engine = engine_map.get(ts.engine_id) if (ts and ts.engine_id) else None

        # Find current drivers (from DriverSeasonStats for latest season)
        team_driver_stats = (
            db.query(m.DriverSeasonStats)
            .filter_by(team_id=t.id, season_id=latest_season_id)
            .limit(2)
            .all()
        )
        assigned_drivers = [driver_map.get(ds.driver_id) for ds in team_driver_stats]
        # Pad to 2 slots, fill None if a seat is empty
        while len(assigned_drivers) < 2:
            assigned_drivers.append(None)
        assigned_drivers = assigned_drivers[:2]

        team = Team(
            name=t.name,
            drivers=assigned_drivers,
            driver_contracts=[3, 3],   # approximate
            chassis=chassis,
            engine=engine if engine else _fallback_engine(engines),
            color_primary=t.color_primary,
            color_secondary=t.color_secondary,
            engine_contract=3,         # approximate
            db_id=t.id,
        )
        team.direction = direction
        teams.append(team)

        # Wire back-references
        for drv in assigned_drivers:
            if drv:
                drv.team = team
        if engine:
            engine.add_team(team)

    driver_gen = DriverGenerator(names_dir=names_dir)
    return tracks, engines, teams, drivers, driver_gen


def _rebuild_direction(db: Session, team_id: int, latest_ts, latest_season_num: int) -> Direction:
    direction = Direction()
    if latest_ts:
        avg = latest_ts.direction_avg
        # Distribute avg equally across the three skill dimensions
        direction.development = avg
        direction.scouting = avg
        direction.eng_scouting = avg
        direction.avg = avg
    # Rebuild last N seasons' positions for firing logic
    history_window = SimulationConstants.HISTORY_YEARS
    past_stats = (
        db.query(m.TeamSeasonStats)
        .filter(
            m.TeamSeasonStats.team_id == team_id,
            m.TeamSeasonStats.championship_position.isnot(None),
        )
        .order_by(m.TeamSeasonStats.season_id.desc())
        .limit(history_window)
        .all()
    )
    direction.position_history = [s.championship_position for s in reversed(past_stats)]
    direction.years = min(len(past_stats), 5)
    return direction


def _fallback_engine(engines) -> Engine:
    """Return the engine with the most capacity (fewest teams) as a fallback."""
    import random
    return random.choice(engines)
