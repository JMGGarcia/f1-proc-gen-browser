"""Module-level DB writer functions extracted from WorldRunner."""
from __future__ import annotations

from db import models as m
from sim.constants import PointsSystem


def write_race_results(db, season_id: int, race_records) -> None:
    points_table = PointsSystem.RACE_POINTS
    for round_num, (track, results) in enumerate(race_records, 1):
        db_race = m.Race(season_id=season_id, track_id=track.db_id, round_number=round_num)
        db.add(db_race)
        db.flush()
        for idx, (driver, time_val) in enumerate(results):
            dnf = time_val == -1.0
            position = 0 if dnf else idx + 1
            pts = 0 if dnf or idx >= len(points_table) else points_table[idx]
            db.add(m.RaceResult(
                race_id=db_race.id,
                driver_id=driver.db_id,
                team_id=driver.team.db_id,
                engine_id=driver.team.engine.db_id,
                position=position,
                points=pts,
                dnf=dnf,
                total_time=None if dnf else time_val,
            ))


def write_race_results_for(db, db_race_id: int, results) -> None:
    """Write RaceResult rows into an already-created Race row (tick-mode)."""
    points_table = PointsSystem.RACE_POINTS
    for idx, (driver, time_val) in enumerate(results):
        dnf = time_val == -1.0
        position = 0 if dnf else idx + 1
        pts = 0 if dnf or idx >= len(points_table) else points_table[idx]
        db.add(m.RaceResult(
            race_id=db_race_id,
            driver_id=driver.db_id,
            team_id=driver.team.db_id,
            engine_id=driver.team.engine.db_id,
            position=position,
            points=pts,
            dnf=dnf,
            total_time=None if dnf else time_val,
        ))


def write_one_race(db, season_id: int, round_num: int, track, results) -> None:
    points_table = PointsSystem.RACE_POINTS
    db_race = m.Race(season_id=season_id, track_id=track.db_id, round_number=round_num)
    db.add(db_race)
    db.flush()
    for idx, (driver, time_val) in enumerate(results):
        dnf = time_val == -1.0
        position = 0 if dnf else idx + 1
        pts = 0 if dnf or idx >= len(points_table) else points_table[idx]
        db.add(m.RaceResult(
            race_id=db_race.id,
            driver_id=driver.db_id,
            team_id=driver.team.db_id,
            engine_id=driver.team.engine.db_id,
            position=position,
            points=pts,
            dnf=dnf,
            total_time=None if dnf else time_val,
        ))


def write_season_stats(db, engines, season_id: int, race_records, sorted_drivers, sorted_teams) -> None:
    driver_points = {d: pts for d, pts in sorted_drivers}
    team_points = {t: pts for t, pts in sorted_teams}

    win_counts: dict = {}
    for _track, results in race_records:
        if results:
            winner = results[0][0]
            win_counts[winner.db_id] = win_counts.get(winner.db_id, 0) + 1

    for pos, (driver, _) in enumerate(sorted_drivers, 1):
        db.add(m.DriverSeasonStats(
            driver_id=driver.db_id,
            season_id=season_id,
            team_id=driver.team.db_id if driver.team else None,
            engine_id=driver.team.engine.db_id if (driver.team and driver.team.engine) else None,
            age=driver.age,
            skill=driver.skill,
            top_skill=driver.top_skill,
            effective_skill=driver.effective_skill,
            total_points=driver_points.get(driver, 0),
            championship_position=pos,
            wins=win_counts.get(driver.db_id, 0),
        ))

    for pos, (team, _) in enumerate(sorted_teams, 1):
        db.add(m.TeamSeasonStats(
            team_id=team.db_id,
            season_id=season_id,
            engine_id=team.engine.db_id if team.engine else None,
            chassis=team.chassis,
            owner_chief_id=team.owner.db_id if team.owner else None,
            cto_chief_id=team.cto.db_id if team.cto else None,
            cmo_chief_id=team.cmo.db_id if team.cmo else None,
            cpo_chief_id=team.cpo.db_id if team.cpo else None,
            owner_skill=team.owner.skill_primary if team.owner else None,
            cto_development=team.cto.skill_primary if team.cto else None,
            cto_eng_scouting=team.cto.skill_secondary if team.cto else None,
            cpo_scouting=team.cpo.skill_primary if team.cpo else None,
            total_points=team_points.get(team, 0),
            championship_position=pos,
            sponsor_id=team.sponsor.db_id if team.sponsor else None,
            owner_type=team.owner_type,
        ))

    for engine in engines:
        db.add(m.EngineSeasonStats(
            engine_id=engine.db_id,
            season_id=season_id,
            power=engine.power,
            reliability=engine.reliability,
        ))


def emit_event(
    db,
    season_num: int,
    event_type: str,
    description: str,
    entities: list[tuple[str, int]] | None = None,
) -> None:
    event = m.WorldEvent(
        season_number=season_num,
        event_type=event_type,
        description=description,
    )
    db.add(event)
    if entities:
        db.flush()
        for entity_type, entity_id in entities:
            db.add(m.WorldEventEntity(
                event_id=event.id,
                entity_type=entity_type,
                entity_id=entity_id,
            ))


def sync_team_to_db(db, team) -> None:
    """Persist current in-memory engine/driver/sponsor state to the Team DB row."""
    engine_id = team.engine.db_id if team.engine else None
    sponsor_id = team.sponsor.db_id if team.sponsor else None
    dc = team.driver_contracts
    db.query(m.Team).filter_by(id=team.db_id).update({
        "engine_id": engine_id,
        "engine_contract": team.engine_contract,
        "driver_contract_1": dc[0] if len(dc) > 0 else None,
        "driver_contract_2": dc[1] if len(dc) > 1 else None,
        "chassis": team.chassis,
        "sponsor_id": sponsor_id,
        "sponsor_contract": team.sponsor_contract,
    })
