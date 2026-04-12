"""
Creates the initial world state in the database and returns in-memory sim objects.
"""

from __future__ import annotations

import colorsys
import json
import os
import random
import uuid
from typing import List, Tuple

from db import models as m
from sim.constants import SimulationConstants, TeamConstants
from sim.countries import get_all_countries, get_country
from sim.drivers import Driver, DriverGenerator
from sim.sponsors import Sponsor
from sim.teams import Chief, ChiefGenerator, ChiefRole, Engine, OwnerType, Team
from sim.tracks import Track


def _load_data(filename: str):
    data_path = os.path.join(os.path.dirname(__file__), "..", "data", filename)
    with open(data_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _make_tracks() -> List[Track]:
    data = _load_data("tracks.json")
    return [Track(
        name=d["name"],
        downforce_over_engine=d["downforce_over_engine"],
        car_over_driver=d["car_over_driver"],
        target_lap_time=d["target_lap_time"],
    ) for d in data]


def _make_engines() -> List[Engine]:
    data = _load_data("engines.json")
    return [
        Engine(name=d["name"], power=random.random(), reliability=0.8,
               color_primary=d["color_primary"], color_secondary=d["color_secondary"],
               nationality=d["nationality"])
        for d in data
    ]


def _make_sponsors() -> List[Sponsor]:
    data = _load_data("sponsors.json")
    return [
        Sponsor(name=d["name"], tier=d["tier"],
                color_primary=d["color_primary"], color_secondary=d["color_secondary"],
                nationality=d["nationality"])
        for d in data
    ]


def _hsl_to_hex(h: float, s: float, l: float) -> str:
    """Convert HSL (0-1 each) to a CSS hex color string."""
    r, g, b = colorsys.hls_to_rgb(h, l, s)  # colorsys uses HLS order
    return "#{:02X}{:02X}{:02X}".format(int(r * 255), int(g * 255), int(b * 255))


def _generate_team_colors(used_hues: list[float]) -> tuple[str, str]:
    """
    Generate a (primary, secondary) color pair that looks like a real racing livery:
    - 70%: rich/deep tone (Ferrari red, Williams navy, forest green) + white text
    - 30%: bold/bright tone (McLaren papaya, Renault yellow, vivid mid-tones) + dark text
    - High saturation (0.60-1.0) so colors are vivid, not washed out
    - Hues spaced at least 30° apart so teams look distinct
    """
    max_attempts = 60
    for _ in range(max_attempts):
        hue = random.random()
        min_dist = min((abs(hue - h) for h in used_hues), default=1.0)
        min_dist = min(min_dist, 1.0 - min_dist)  # wrap-around distance
        if used_hues and min_dist < 0.083:  # ~30° on the 360° wheel
            continue

        sat = random.uniform(0.60, 1.0)

        # Accent options for dark backgrounds (replaces white occasionally)
        dark_bg_accents = [
            "#F0F0F0",  # white (most common)
            "#F0F0F0",
            "#F0F0F0",
            "#FFD700",  # bright yellow
            "#FFB300",  # gold
            "#FF8C00",  # dark orange
            "#00E5FF",  # cyan
            "#C0C0C0",  # silver
        ]
        # Accent options for light backgrounds (replaces black occasionally)
        light_bg_accents = [
            "#111111",  # black (most common)
            "#111111",
            "#111111",
            "#0A1628",  # deep navy
            "#1A0A0A",  # dark burgundy
            "#0A1A0A",  # dark forest
        ]

        if random.random() < 0.70:
            # Rich, deep tone — Ferrari red, Williams navy, dark green
            lightness = random.uniform(0.20, 0.38)
            primary = _hsl_to_hex(hue, sat, lightness)
            secondary = random.choice(dark_bg_accents)
        else:
            # Bold, bright tone — McLaren papaya, Renault yellow
            lightness = random.uniform(0.45, 0.60)
            primary = _hsl_to_hex(hue, sat, lightness)
            secondary = random.choice(light_bg_accents)

        used_hues.append(hue)
        return primary, secondary

    # Fallback
    used_hues.append(random.random())
    return "#1C2340", "#F0F0F0"


def _generate_team_configs(names_dir: str, n: int = 10) -> list[tuple[str, str, str, str]]:
    """
    Generate n team configs: (name, nationality, color_primary, color_secondary).
    Names are drawn from last name files, one per nationality to spread teams around.
    Nationalities are weighted by millionaires, interest, and infrastructure (excluding population).
    """
    # All available nationalities
    all_nats = [d for d in os.listdir(names_dir) if os.path.isdir(os.path.join(names_dir, d))]
    
    # Calculate weights based on pre-computed team weights (excluding population)
    weights = []
    for nat_code in all_nats:
        country = get_country(nat_code)
        if country:
            weights.append(country.team_weight)
        else:
            weights.append(1.0)
    
    # Weighted selection of nationalities
    nats = random.choices(all_nats, weights=weights, k=n)

    used_names: set[str] = set()
    used_hues: list[float] = []
    configs = []

    for nat in nats:
        last_path = os.path.join(names_dir, nat, "last.txt")
        with open(last_path) as f:
            surnames = [s.strip() for s in f.readlines() if s.strip()]

        # Pick a unique surname
        candidates = [s for s in surnames if s not in used_names]
        if not candidates:
            candidates = surnames
        name = random.choice(candidates)
        used_names.add(name)

        cp, cs = _generate_team_colors(used_hues)
        configs.append((name, nat, cp, cs))

    return configs


def seed_world(db, names_dir: str = "./names") -> Tuple[
    List[Track], List[Engine], List[Team], List[Driver], DriverGenerator, List[Sponsor],
    ChiefGenerator, List[Chief]
]:
    """Seed tracks, engines, teams, sponsors and initial driver pool into the DB.
    Returns sim objects with db_id fields populated."""

    # --- Sponsors ---
    sponsors: List[Sponsor] = _make_sponsors()
    for sp in sponsors:
        db.add(m.Sponsor(
            name=sp.name, tier=sp.tier,
            color_primary=sp.color_primary, color_secondary=sp.color_secondary,
            nationality=sp.nationality,
        ))
    db.flush()
    all_db_sponsors = db.query(m.Sponsor).order_by(m.Sponsor.id).all()
    sponsor_map: dict[int, Sponsor] = {}
    for sp, db_sp in zip(sponsors, all_db_sponsors):
        sp.db_id = db_sp.id
        sponsor_map[db_sp.id] = sp

    # --- Tracks ---
    tracks: List[Track] = _make_tracks()
    for track in tracks:
        db_track = m.Track(
            name=track.name,
            downforce_over_engine=track.downforce_over_engine,
            car_over_driver=track.car_over_driver,
            target_lap_time=track.target_lap_time,
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
            nationality=engine.nationality,
        )
        db.add(db_engine)
    db.flush()
    all_db_engines = db.query(m.Engine).order_by(m.Engine.id).all()
    for engine, db_engine in zip(engines, all_db_engines):
        engine.db_id = db_engine.id

    # --- Drivers ---
    driver_gen = DriverGenerator(names_dir=names_dir)
    track_ids = [t.db_id for t in tracks]
    drivers: List[Driver] = [driver_gen.generate_driver(track_ids=track_ids) for _ in range(SimulationConstants.DRIVERS_POOL)]

    for driver in drivers:
        db_driver = m.Driver(
            first_name=driver.first_name,
            last_name=driver.last_name,
            nationality=driver.nationality,
            age=driver.age,
            skill=driver.skill,
            top_skill=driver.top_skill,
            loyalty=driver.loyalty,
            greed=driver.greed,
            ambition=driver.ambition,
            liked_tracks=",".join(str(i) for i in driver.liked_track_ids) if driver.liked_track_ids else None,
            disliked_tracks=",".join(str(i) for i in driver.disliked_track_ids) if driver.disliked_track_ids else None,
        )
        db.add(db_driver)
    db.flush()
    all_db_drivers = db.query(m.Driver).order_by(m.Driver.id).all()
    for driver, db_driver in zip(drivers, all_db_drivers):
        driver.db_id = db_driver.id

    # --- Teams ---
    team_configs = _generate_team_configs(names_dir, n=10)
    teams: List[Team] = []
    driver_idx = 0

    # Shuffle sponsors so initial assignment is random across tiers
    shuffled_sponsors = sponsors[:]
    random.shuffle(shuffled_sponsors)
    sponsor_pool = iter(shuffled_sponsors)

    for team_name, nat, cp, cs in team_configs:
        drv1 = drivers[driver_idx]
        drv2 = drivers[driver_idx + 1]
        driver_idx += 2

        engine = random.choice(engines)
        engine_contract = random.randint(1, 5)

        # Assign one sponsor per team from the shuffled pool
        team_sponsor = next(sponsor_pool)
        sponsor_contract = random.randint(3, 6)

        finance_base = random.randint(TeamConstants.FINANCE_BASE_INDIVIDUAL_MIN, TeamConstants.FINANCE_BASE_INDIVIDUAL_MAX)
        team = Team(
            name=team_name,
            drivers=[drv1, drv2],
            driver_contracts=[random.randint(1, 4), random.randint(1, 4)],
            chassis=random.random() * 0.9,
            engine=engine,
            color_primary=cp,
            color_secondary=cs,
            engine_contract=engine_contract,
            sponsor=team_sponsor,
            sponsor_contract=sponsor_contract,
            nationality=nat,
            finance_base=finance_base,
        )
        teams.append(team)
        engine.add_team(team)
        team_sponsor.assign_team(team)
        team.assign_driver(drv1, 0)
        team.assign_driver(drv2, 1)

        db_team = m.Team(
            name=team_name, color_primary=cp, color_secondary=cs, nationality=nat,
            sponsor_id=team_sponsor.db_id, sponsor_contract=sponsor_contract,
            engine_id=engine.db_id, engine_contract=engine_contract,
            driver_contract_1=team.driver_contracts[0],
            driver_contract_2=team.driver_contracts[1],
            chassis=team.chassis,
            is_active=True, owner_type="individual", finance_base=finance_base,
        )
        db.add(db_team)

    db.flush()
    all_db_teams = db.query(m.Team).order_by(m.Team.id).all()
    for team, db_team in zip(teams, all_db_teams):
        team.db_id = db_team.id

    db.add(m.WorldMeta(world_id=str(uuid.uuid4())))
    db.commit()

    # --- Chiefs ---
    chief_gen = ChiefGenerator(names_dir=names_dir)
    all_chiefs: List[Chief] = []
    for team in teams:
        owner = chief_gen.generate_owner(team.name, team.nationality, OwnerType.INDIVIDUAL)
        cto   = chief_gen.generate_cto(team.nationality)
        cmo   = chief_gen.generate_cmo(team.nationality)
        cpo   = chief_gen.generate_cpo(team.nationality)
        for chief in (owner, cto, cmo, cpo):
            chief.team = team
            db_chief = m.TeamChief(
                first_name=chief.first_name,
                last_name=chief.last_name,
                nationality=chief.nationality,
                role=chief.role,
                age=chief.age,
                skill_primary=chief.skill_primary,
                skill_secondary=chief.skill_secondary,
                team_id=team.db_id,
                contract_years=chief.contract_years,
                retired=False,
            )
            db.add(db_chief)
            db.flush()
            chief.db_id = db_chief.id
            all_chiefs.append(chief)
        team.owner = owner
        team.cto   = cto
        team.cmo   = cmo
        team.cpo   = cpo
    db.commit()

    # --- Free-agent chief pool (CTO / CMO / CPO) ---
    all_country_codes = [c.code for c in get_all_countries()]
    for role_attr, gen_method in (
        ("cto", chief_gen.generate_cto),
        ("cmo", chief_gen.generate_cmo),
        ("cpo", chief_gen.generate_cpo),
    ):
        for _ in range(TeamConstants.CHIEFS_POOL_PER_ROLE):
            nat = random.choice(all_country_codes)
            chief = gen_method(nat)
            chief.team = None
            db_chief = m.TeamChief(
                first_name=chief.first_name,
                last_name=chief.last_name,
                nationality=chief.nationality,
                role=chief.role,
                age=chief.age,
                skill_primary=chief.skill_primary,
                skill_secondary=chief.skill_secondary,
                team_id=None,
                contract_years=chief.contract_years,
                retired=False,
            )
            db.add(db_chief)
            db.flush()
            chief.db_id = db_chief.id
            all_chiefs.append(chief)
    db.commit()

    return tracks, engines, teams, drivers, driver_gen, sponsors, chief_gen, all_chiefs
