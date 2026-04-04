from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from db.models import Driver, DriverSeasonStats, Season, Team, TeamSeasonStats, WorldEvent
from db.session import get_db_session
from sim.flags import NATIONALITY_FLAGS
from web import sim_state

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")


@router.get("/")
def index(request: Request, db: Session = Depends(get_db_session)):
    recent_events = (
        db.query(WorldEvent)
        .order_by(WorldEvent.id.desc())
        .limit(40)
        .all()
    )
    recent_seasons = (
        db.query(Season)
        .filter_by(completed=True)
        .order_by(Season.number.desc())
        .limit(5)
        .all()
    )
    total_seasons = db.query(Season).filter_by(completed=True).count()

    summaries = []
    for s in recent_seasons:
        drv = db.query(DriverSeasonStats).filter_by(season_id=s.id, championship_position=1).first()
        tm = db.query(TeamSeasonStats).filter_by(season_id=s.id, championship_position=1).first()
        driver_obj = db.query(Driver).filter_by(id=drv.driver_id).first() if drv else None
        team_obj = db.query(Team).filter_by(id=tm.team_id).first() if tm else None
        summaries.append({"season": s, "driver": driver_obj, "team": team_obj})

    return templates.TemplateResponse(request, "index.html", {
        "recent_events": recent_events,
        "summaries": summaries,
        "total_seasons": total_seasons,
        "sim_available": sim_state.is_available(),
        "sim_busy": sim_state.is_busy(),
    })
