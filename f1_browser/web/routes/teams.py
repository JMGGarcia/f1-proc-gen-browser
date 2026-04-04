from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from db.models import Driver, DriverSeasonStats, Engine, EngineSeasonStats, Season, Team, TeamSeasonStats
from db.session import get_db_session
from sim.flags import NATIONALITY_FLAGS

router = APIRouter(prefix="/teams")
templates = Jinja2Templates(directory="web/templates")


@router.get("/")
def teams_list(request: Request, db: Session = Depends(get_db_session)):
    teams = db.query(Team).order_by(Team.name).all()
    for team in teams:
        latest = (
            db.query(TeamSeasonStats)
            .filter_by(team_id=team.id)
            .order_by(TeamSeasonStats.season_id.desc())
            .first()
        )
        team.latest_stats = latest
        team.current_engine = (
            db.query(Engine).filter_by(id=latest.engine_id).first()
            if latest and latest.engine_id else None
        )

    return templates.TemplateResponse(request, "teams_list.html", {
        "teams": teams,
    })


@router.get("/{team_id}")
def team_detail(team_id: int, request: Request, db: Session = Depends(get_db_session)):
    team = db.query(Team).filter_by(id=team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    season_history = (
        db.query(TeamSeasonStats)
        .filter_by(team_id=team_id)
        .order_by(TeamSeasonStats.season_id)
        .all()
    )
    for entry in season_history:
        entry.season_obj = db.query(Season).filter_by(id=entry.season_id).first()
        entry.engine_obj = db.query(Engine).filter_by(id=entry.engine_id).first() if entry.engine_id else None
        if entry.engine_id:
            eng_s = db.query(EngineSeasonStats).filter_by(
                engine_id=entry.engine_id, season_id=entry.season_id
            ).first()
            entry.engine_power = int(eng_s.power * 100) if eng_s else None
        else:
            entry.engine_power = None

    driver_stints = (
        db.query(DriverSeasonStats)
        .filter_by(team_id=team_id)
        .order_by(DriverSeasonStats.season_id)
        .all()
    )
    seen_drivers: set = set()
    unique_drivers = []
    for stint in driver_stints:
        if stint.driver_id not in seen_drivers:
            seen_drivers.add(stint.driver_id)
            drv = db.query(Driver).filter_by(id=stint.driver_id).first()
            if drv:
                unique_drivers.append(drv)

    for drv in unique_drivers:
        drv.flag = NATIONALITY_FLAGS.get(drv.nationality, "")

    total_wins = sum(e.championship_position == 1 for e in season_history)

    return templates.TemplateResponse(request, "team_detail.html", {
        "team": team,
        "season_history": season_history,
        "unique_drivers": unique_drivers,
        "total_wins": total_wins,
    })
