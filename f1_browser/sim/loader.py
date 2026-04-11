"""Reconstruct in-memory sim objects from the existing database state."""
from __future__ import annotations

import random
from sqlalchemy.orm import Session

from db import models as m
from sim.constants import SimulationConstants
from sim.drivers import Driver, DriverGenerator
from sim.sponsors import Sponsor
from sim.teams import Chief, ChiefGenerator, ChiefRole, Engine, OwnerType, Team
from sim.tracks import Track


def load_world_from_db(db: Session, names_dir: str = "./names"):
    # ── Sponsors ─────────────────────────────────────────────────────────
    db_sponsors = db.query(m.Sponsor).order_by(m.Sponsor.id).all()
    sponsors = []
    sponsor_map: dict[int, Sponsor] = {}
    for s in db_sponsors:
        sp = Sponsor(
            name=s.name, tier=s.tier,
            color_primary=s.color_primary, color_secondary=s.color_secondary,
            db_id=s.id, nationality=s.nationality,
        )
        sponsors.append(sp)
        sponsor_map[s.id] = sp

    # ── Tracks ──────────────────────────────────────────────────────────
    db_tracks = db.query(m.Track).order_by(m.Track.id).all()
    tracks = [
        Track(
            name=t.name,
            downforce_over_engine=t.downforce_over_engine,
            car_over_driver=t.car_over_driver,
            target_lap_time=t.target_lap_time if t.target_lap_time else 90.0,
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
            nationality=e.nationality,
        )
        engines.append(eng)
        engine_map[e.id] = eng

    # ── Active drivers ───────────────────────────────────────────────────
    db_drivers = db.query(m.Driver).filter_by(retired=False).all()
    drivers: list[Driver] = []
    driver_map: dict[int, Driver] = {}
    for d in db_drivers:
        # Use Driver-row values — _age_drivers() keeps these current every season,
        # including for free agents who haven't raced in many seasons.
        # DriverSeasonStats would give stale values for long-term free agents.
        skill = d.skill if d.skill is not None else 0.3
        age = d.age if d.age is not None else 25
        top_skill = d.top_skill if d.top_skill is not None else skill

        drv = Driver(
            db_id=d.id,
            first_name=d.first_name,
            last_name=d.last_name,
            country=d.nationality,
            skill=skill,
            age=age,
            loyalty=d.loyalty if d.loyalty is not None else 0.5,
            greed=d.greed if d.greed is not None else 0.5,
            ambition=d.ambition if d.ambition is not None else 0.5,
        )
        drv.base_skill = skill
        drv.top_skill = top_skill
        if d.liked_tracks:
            drv.liked_track_ids = [int(x) for x in d.liked_tracks.split(",") if x.strip()]
        if d.disliked_tracks:
            drv.disliked_track_ids = [int(x) for x in d.disliked_tracks.split(",") if x.strip()]
        drivers.append(drv)
        driver_map[d.id] = drv

    # ── Teams ────────────────────────────────────────────────────────────
    db_teams = db.query(m.Team).filter_by(is_active=True).order_by(m.Team.id).all()
    teams: list[Team] = []
    for t in db_teams:
        ts = (
            db.query(m.TeamSeasonStats)
            .filter_by(team_id=t.id, season_id=latest_season_id)
            .first()
        )
        chassis = ts.chassis if ts else 0.5

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

        sponsor = sponsor_map.get(t.sponsor_id) if t.sponsor_id else None
        sponsor_contract = t.sponsor_contract if t.sponsor_contract else 3
        owner_engine = engine_map.get(t.owner_engine_id) if t.owner_engine_id else None
        owner_sponsor = sponsor_map.get(t.owner_sponsor_id) if t.owner_sponsor_id else None

        team = Team(
            name=t.name,
            drivers=assigned_drivers,
            driver_contracts=[
                t.driver_contract_1 if t.driver_contract_1 is not None else 3,
                t.driver_contract_2 if t.driver_contract_2 is not None else 3,
            ],
            chassis=chassis,
            engine=engine if engine else _fallback_engine(engines),
            color_primary=t.color_primary,
            color_secondary=t.color_secondary,
            engine_contract=t.engine_contract if t.engine_contract is not None else 3,
            sponsor=sponsor,
            sponsor_contract=sponsor_contract,
            db_id=t.id,
            nationality=t.nationality,
            owner_type=t.owner_type or OwnerType.INDIVIDUAL,
            owner_engine=owner_engine,
            owner_sponsor=owner_sponsor,
            finance_base=t.finance_base if t.finance_base is not None else 2,
        )
        teams.append(team)

        # Wire back-references
        for drv in assigned_drivers:
            if drv:
                drv.team = team
        if engine:
            engine.add_team(team)
        if sponsor:
            sponsor.assign_team(team)

    # ── Rebuild position_history for each team ───────────────────────────
    history_window = SimulationConstants.HISTORY_YEARS
    for team in teams:
        past_stats = (
            db.query(m.TeamSeasonStats)
            .filter(
                m.TeamSeasonStats.team_id == team.db_id,
                m.TeamSeasonStats.championship_position.isnot(None),
            )
            .order_by(m.TeamSeasonStats.season_id.desc())
            .limit(history_window)
            .all()
        )
        team.position_history = [s.championship_position for s in reversed(past_stats)]

    # ── Collect all non-retired chiefs (including free agents) ───────────
    chief_gen = ChiefGenerator(names_dir=names_dir)
    all_chiefs: list[Chief] = []
    team_map_by_id = {team.db_id: team for team in teams}
    db_active_chiefs = db.query(m.TeamChief).filter_by(retired=False).all()
    for dc in db_active_chiefs:
        chief = Chief(
            db_id=dc.id,
            role=dc.role,
            first_name=dc.first_name or "",
            last_name=dc.last_name,
            country=dc.nationality,
            age=dc.age,
            skill_primary=dc.skill_primary,
            skill_secondary=dc.skill_secondary,
            contract_years=dc.contract_years,
        )
        assigned_team = team_map_by_id.get(dc.team_id) if dc.team_id else None
        chief.team = assigned_team
        all_chiefs.append(chief)
        # Wire to team's role slot
        if assigned_team is not None:
            if dc.role == ChiefRole.OWNER:
                assigned_team.owner = chief
            elif dc.role == ChiefRole.CTO:
                assigned_team.cto = chief
            elif dc.role == ChiefRole.CMO:
                assigned_team.cmo = chief
            elif dc.role == ChiefRole.CPO:
                assigned_team.cpo = chief

    driver_gen = DriverGenerator(names_dir=names_dir)
    return tracks, engines, teams, drivers, driver_gen, sponsors, chief_gen, all_chiefs


def _fallback_engine(engines) -> Engine:
    """Return the engine with the most capacity (fewest teams) as a fallback."""
    return random.choice(engines)
