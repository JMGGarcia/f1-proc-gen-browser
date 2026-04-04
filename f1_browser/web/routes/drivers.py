from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from db.models import Driver, DriverSeasonStats, Engine, Race, RaceResult, Season, Team
from db.session import get_db_session
from sim.flags import NATIONALITY_FLAGS

router = APIRouter(prefix="/drivers")
templates = Jinja2Templates(directory="web/templates")


def _enrich_active(driver, db):
    latest = (
        db.query(DriverSeasonStats)
        .filter_by(driver_id=driver.id)
        .order_by(DriverSeasonStats.season_id.desc())
        .first()
    )
    driver.latest_stats = latest
    driver.current_team = (
        db.query(Team).filter_by(id=latest.team_id).first()
        if latest and latest.team_id else None
    )
    # For drivers not yet in a season (no stats), fall back to Driver columns
    driver.display_age = (latest.age if latest else driver.age) or "—"
    driver.display_skill = (int((latest.skill if latest else driver.skill or 0) * 100)) if (latest or driver.skill) else "—"
    driver.flag = NATIONALITY_FLAGS.get(driver.nationality, "")
    return driver


def _enrich_retired(driver, db):
    last = (
        db.query(DriverSeasonStats)
        .filter_by(driver_id=driver.id)
        .order_by(DriverSeasonStats.season_id.desc())
        .first()
    )
    driver.last_stats = last
    driver.flag = NATIONALITY_FLAGS.get(driver.nationality, "")
    return driver


@router.get("/")
def drivers_list(request: Request, db: Session = Depends(get_db_session)):
    # All non-retired drivers (including those without a team yet)
    active_drivers = [
        _enrich_active(d, db)
        for d in db.query(Driver).filter_by(retired=False).order_by(Driver.last_name).all()
    ]

    return templates.TemplateResponse(request, "drivers_list.html", {
        "active_drivers": active_drivers,
    })


@router.get("/retired")
def drivers_retired(request: Request, db: Session = Depends(get_db_session)):
    retired_drivers = [
        _enrich_retired(d, db)
        for d in db.query(Driver).filter_by(retired=True).order_by(Driver.last_name).all()
    ]

    return templates.TemplateResponse(request, "drivers_retired.html", {
        "retired_drivers": retired_drivers,
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
    for entry in career:
        entry.season_obj = db.query(Season).filter_by(id=entry.season_id).first()
        entry.team_obj = db.query(Team).filter_by(id=entry.team_id).first() if entry.team_id else None
        entry.engine_obj = db.query(Engine).filter_by(id=entry.engine_id).first() if entry.engine_id else None

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
