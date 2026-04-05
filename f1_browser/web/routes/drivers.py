from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from db.models import Driver, DriverSeasonStats, Engine, Race, RaceResult, Season, Team
from db.session import get_db_session
from sim.flags import NATIONALITY_FLAGS
from web.templates_env import templates

router = APIRouter(prefix="/drivers")


@router.get("/")
def drivers_list(request: Request, db: Session = Depends(get_db_session)):
    drivers = db.query(Driver).filter_by(retired=False).order_by(Driver.last_name).all()
    driver_ids = [d.id for d in drivers]

    # Latest season stats per driver (max season_id subquery)
    latest_sid_per_driver = (
        db.query(
            DriverSeasonStats.driver_id,
            func.max(DriverSeasonStats.season_id).label("max_sid"),
        )
        .filter(DriverSeasonStats.driver_id.in_(driver_ids))
        .group_by(DriverSeasonStats.driver_id)
        .subquery()
    )
    latest_dss_rows = (
        db.query(DriverSeasonStats)
        .join(
            latest_sid_per_driver,
            (DriverSeasonStats.driver_id == latest_sid_per_driver.c.driver_id)
            & (DriverSeasonStats.season_id == latest_sid_per_driver.c.max_sid),
        )
        .all()
    )
    latest_dss_by_driver = {row.driver_id: row for row in latest_dss_rows}

    team_ids = list({row.team_id for row in latest_dss_rows if row.team_id})
    teams_by_id = {t.id: t for t in db.query(Team).filter(Team.id.in_(team_ids)).all()}

    for d in drivers:
        last_stats = latest_dss_by_driver.get(d.id)
        d.latest_stats = last_stats
        d.current_team = teams_by_id.get(last_stats.team_id) if last_stats and last_stats.team_id else None
        d.display_age = (last_stats.age if last_stats else d.age) or "—"
        d.display_skill = (
            int((last_stats.skill if last_stats else d.skill or 0) * 100)
        ) if (last_stats or d.skill) else "—"
        d.flag = NATIONALITY_FLAGS.get(d.nationality, "")

    return templates.TemplateResponse(request, "drivers_list.html", {
        "active_drivers": drivers,
    })


@router.get("/retired")
def drivers_retired(request: Request, db: Session = Depends(get_db_session)):
    drivers = db.query(Driver).filter_by(retired=True).order_by(Driver.last_name).all()
    driver_ids = [d.id for d in drivers]

    # Latest season stats per retired driver (one subquery)
    latest_sid_per_driver = (
        db.query(
            DriverSeasonStats.driver_id,
            func.max(DriverSeasonStats.season_id).label("max_sid"),
        )
        .filter(DriverSeasonStats.driver_id.in_(driver_ids))
        .group_by(DriverSeasonStats.driver_id)
        .subquery()
    )
    latest_dss_rows = (
        db.query(DriverSeasonStats)
        .join(
            latest_sid_per_driver,
            (DriverSeasonStats.driver_id == latest_sid_per_driver.c.driver_id)
            & (DriverSeasonStats.season_id == latest_sid_per_driver.c.max_sid),
        )
        .all()
    )
    latest_dss_by_driver = {row.driver_id: row for row in latest_dss_rows}

    for d in drivers:
        d.last_stats = latest_dss_by_driver.get(d.id)
        d.flag = NATIONALITY_FLAGS.get(d.nationality, "")

    return templates.TemplateResponse(request, "drivers_retired.html", {
        "retired_drivers": drivers,
    })


@router.get("/{driver_id}")
def driver_detail(driver_id: int, request: Request, db: Session = Depends(get_db_session)):
    driver = db.query(Driver).filter_by(id=driver_id).first()
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    driver.flag = NATIONALITY_FLAGS.get(driver.nationality, "")

    career = (
        db.query(DriverSeasonStats)
        .filter_by(driver_id=driver_id)
        .order_by(DriverSeasonStats.season_id)
        .all()
    )

    # Batch-fetch all referenced objects for career
    career_season_ids = [e.season_id for e in career]
    career_team_ids = list({e.team_id for e in career if e.team_id})
    career_engine_ids = list({e.engine_id for e in career if e.engine_id})
    seasons_by_id = {s.id: s for s in db.query(Season).filter(Season.id.in_(career_season_ids)).all()}
    career_teams = {t.id: t for t in db.query(Team).filter(Team.id.in_(career_team_ids)).all()}
    career_engines = {e.id: e for e in db.query(Engine).filter(Engine.id.in_(career_engine_ids)).all()}

    for entry in career:
        entry.season_obj = seasons_by_id.get(entry.season_id)
        entry.team_obj = career_teams.get(entry.team_id) if entry.team_id else None
        entry.engine_obj = career_engines.get(entry.engine_id) if entry.engine_id else None

    total_wins = db.query(RaceResult).filter_by(driver_id=driver_id, position=1).count()
    total_podiums = (
        db.query(RaceResult)
        .filter(RaceResult.driver_id == driver_id, RaceResult.position <= 3, RaceResult.dnf == False)
        .count()
    )

    return templates.TemplateResponse(request, "driver_detail.html", {
        "driver": driver,
        "career": career,
        "total_wins": total_wins,
        "total_podiums": total_podiums,
    })
