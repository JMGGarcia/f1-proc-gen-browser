from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from db.models import Driver, DriverSeasonStats, Engine, Season, Sponsor, Team, TeamSeasonStats, WorldEvent
from db.session import get_db_session
from sim.flags import NATIONALITY_FLAGS
from web import sim_state
from web.templates_env import templates

router = APIRouter()


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
        driver_obj = db.query(Driver).filter_by(id=drv.driver_id).first() if drv else None
        driver_team_obj = db.query(Team).filter_by(id=drv.team_id).first() if (drv and drv.team_id) else None
        driver_engine_obj = db.query(Engine).filter_by(id=drv.engine_id).first() if (drv and drv.engine_id) else None
        driver_sponsor_obj = None
        
        # Get sponsor from team's season stats
        if driver_team_obj and drv:
            sponsor_stats = db.query(TeamSeasonStats).filter_by(team_id=driver_team_obj.id, season_id=s.id).first()
            if sponsor_stats and sponsor_stats.sponsor_id:
                driver_sponsor_obj = db.query(Sponsor).filter_by(id=sponsor_stats.sponsor_id).first()
        
        summaries.append({
            "season": s,
            "driver": driver_obj,
            "driver_team": driver_team_obj,
            "driver_engine": driver_engine_obj,
            "driver_sponsor": driver_sponsor_obj,
        })

    return templates.TemplateResponse(request, "index.html", {
        "recent_events": recent_events,
        "summaries": summaries,
        "total_seasons": total_seasons,
        "sim_available": sim_state.is_available(),
        "sim_busy": sim_state.is_busy(),
    })
