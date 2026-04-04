"""
Creates the initial world state in the database and returns in-memory sim objects.
"""

from __future__ import annotations

import os
import random
from typing import List, Tuple

from db import models as m
from sim.drivers import Driver, DriverGenerator
from sim.teams import Engine, Team
from sim.tracks import Track


def _make_tracks() -> List[Track]:
    data = [
        ("Melbourne",    0.6,  0.8),
        ("Shanghai",     0.5,  0.8),
        ("Bahrain",      0.7,  0.8),
        ("Sochi",        0.3,  0.8),
        ("Barcelona",    0.6,  0.8),
        ("Monaco",       0.9,  0.8),
        ("Montreal",     0.2,  0.8),
        ("Baku",         0.1,  0.8),
        ("Spielberg",    0.2,  0.8),
        ("Silverstone",  0.5,  0.8),
        ("Budapest",     0.75, 0.8),
        ("Spa",          0.1,  0.8),
        ("Monza",        0.1,  0.8),
        ("Singapore",    0.85, 0.8),
        ("Kuala Lumpur", 0.35, 0.8),
        ("Suzuka",       0.6,  0.8),
        ("Austin",       0.4,  0.8),
        ("Mexico City",  0.4,  0.8),
        ("Sao Paulo",    0.25, 0.8),
        ("Abu Dhabi",    0.65, 0.8),
    ]
    return [Track(name=n, downforce_over_engine=d, car_over_driver=c) for n, d, c in data]


def _make_engines() -> List[Engine]:
    # (name, color_primary, color_secondary)
    data = [
        ("SEAT",      "#CC0000", "#303030"),
        ("Chevrolet", "#FFD700", "#303030"),
        ("Jaguar",    "#006400", "#FFD700"),
        ("Audi",      "#303030", "#FFFFFF"),
        ("Toyota",    "#CC0000", "#FFFFFF"),
        ("Ford",      "#003087", "#FFFFFF"),
        ("Hyundai",   "#002C5F", "#FFFFFF"),
        ("BMW",       "#0066CC", "#FFFFFF"),
        ("Honda",     "#1B1B1B", "#FFFFFF"),
        ("Renault",   "#FFE000", "#000000"),
        ("Mercedes",  "#00D2BE", "#000000"),
        ("Ferrari",   "#DC0000", "#FFD700"),
    ]
    return [
        Engine(name=n, power=random.random(), reliability=0.8,
               color_primary=cp, color_secondary=cs)
        for n, cp, cs in data
    ]


def _make_team_configs() -> list:
    # (name, color_primary, color_secondary)
    return [
        ("Lucky Strike",  "#CC0000", "#F5DEB3"),
        ("Marlboro",      "#FF0000", "#FFFFFF"),
        ("Phillip Morris","#1E90FF", "#FFFFFF"),
        ("Chesterfield",  "#FF8700", "#000000"),
        ("Camel",         "#D4AF37", "#003087"),
        ("Newport",       "#20B2AA", "#000000"),
        ("Winston",       "#00008B", "#FFFFFF"),
        ("West",          "#8B0000", "#C0C0C0"),
        ("Rothmans",      "#00005F", "#FFD700"),
        ("Pall Mall",     "#006400", "#FFFFFF"),
    ]


def seed_world(db, names_dir: str = "./names") -> Tuple[
    List[Track], List[Engine], List[Team], List[Driver], DriverGenerator
]:
    """Seed tracks, engines, teams and initial driver pool into the DB.
    Returns sim objects with db_id fields populated."""

    # --- Tracks ---
    tracks: List[Track] = _make_tracks()
    for track in tracks:
        db_track = m.Track(
            name=track.name,
            downforce_over_engine=track.downforce_over_engine,
            car_over_driver=track.car_over_driver,
        )
        db.add(db_track)
    db.flush()
    all_db_tracks = db.query(m.Track).order_by(m.Track.id).all()
    for track, db_track in zip(tracks, all_db_tracks):
        track.db_id = db_track.id

    # --- Engines ---
    engines: List[Engine] = _make_engines()
    for engine in engines:
        db_engine = m.Engine(
            name=engine.name,
            power=engine.power,
            reliability=engine.reliability,
            value=engine.value,
            color_primary=engine.color_primary,
            color_secondary=engine.color_secondary,
        )
        db.add(db_engine)
    db.flush()
    all_db_engines = db.query(m.Engine).order_by(m.Engine.id).all()
    for engine, db_engine in zip(engines, all_db_engines):
        engine.db_id = db_engine.id

    # --- Drivers ---
    driver_gen = DriverGenerator(names_dir=names_dir)
    from sim.constants import SimulationConstants
    drivers: List[Driver] = [driver_gen.generate_driver() for _ in range(SimulationConstants.DRIVERS_POOL)]

    for driver in drivers:
        db_driver = m.Driver(
            first_name=driver.first_name,
            last_name=driver.last_name,
            nationality=driver.nationality,
            age=driver.age,
            skill=driver.skill,
            top_skill=driver.top_skill,
        )
        db.add(db_driver)
    db.flush()
    all_db_drivers = db.query(m.Driver).order_by(m.Driver.id).all()
    for driver, db_driver in zip(drivers, all_db_drivers):
        driver.db_id = db_driver.id

    # --- Teams ---
    team_configs = _make_team_configs()
    teams: List[Team] = []
    driver_idx = 0

    for i, (team_name, cp, cs) in enumerate(team_configs):
        drv1 = drivers[driver_idx]
        drv2 = drivers[driver_idx + 1]
        driver_idx += 2

        engine = random.choice(engines)
        contract_years = random.randint(1, 5)

        team = Team(
            name=team_name,
            drivers=[drv1, drv2],
            driver_contracts=[random.randint(1, 4), random.randint(1, 4)],
            chassis=random.random() * 0.9,
            engine=engine,
            color_primary=cp,
            color_secondary=cs,
            engine_contract=contract_years,
        )
        teams.append(team)
        engine.add_team(team)
        drv1.team = team
        drv2.team = team

        db_team = m.Team(name=team_name, color_primary=cp, color_secondary=cs)
        db.add(db_team)

    db.flush()
    all_db_teams = db.query(m.Team).order_by(m.Team.id).all()
    for team, db_team in zip(teams, all_db_teams):
        team.db_id = db_team.id

    db.commit()
    return tracks, engines, teams, drivers, driver_gen
