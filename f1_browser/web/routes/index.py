from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from db.models import Driver, DriverSeasonStats, Engine, Season, Sponsor, Team, TeamSeasonStats, WorldEvent
from db.session import get_db_session
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

    # Batch-fetch all champion driver stats for recent seasons
    season_ids = [s.id for s in recent_seasons]
    champ_stats_list = (
        db.query(DriverSeasonStats)
        .filter(
            DriverSeasonStats.season_id.in_(season_ids),
            DriverSeasonStats.championship_position == 1,
        )
        .all()
    )
    champ_by_season = {cs.season_id: cs for cs in champ_stats_list}

    # Collect IDs for batch fetches
    driver_ids = [cs.driver_id for cs in champ_stats_list if cs.driver_id]
    team_ids = list({cs.team_id for cs in champ_stats_list if cs.team_id})
    engine_ids = list({cs.engine_id for cs in champ_stats_list if cs.engine_id})

    drivers_by_id = {d.id: d for d in db.query(Driver).filter(Driver.id.in_(driver_ids)).all()}
    teams_by_id = {t.id: t for t in db.query(Team).filter(Team.id.in_(team_ids)).all()}
    engines_by_id = {e.id: e for e in db.query(Engine).filter(Engine.id.in_(engine_ids)).all()}

    # Batch-fetch sponsor data via TeamSeasonStats
    tss_list = (
        db.query(TeamSeasonStats)
        .filter(
            TeamSeasonStats.team_id.in_(team_ids),
            TeamSeasonStats.season_id.in_(season_ids),
        )
        .all()
    )
    sponsor_ids = list({tss.sponsor_id for tss in tss_list if tss.sponsor_id})
    sponsors_by_id = {s.id: s for s in db.query(Sponsor).filter(Sponsor.id.in_(sponsor_ids)).all()}
    # (team_id, season_id) -> sponsor
    sponsor_by_team_season = {
        (tss.team_id, tss.season_id): sponsors_by_id.get(tss.sponsor_id)
        for tss in tss_list
        if tss.sponsor_id
    }

    summaries = []
    for s in recent_seasons:
        drv = champ_by_season.get(s.id)
        driver_obj = drivers_by_id.get(drv.driver_id) if drv else None
        driver_team_obj = teams_by_id.get(drv.team_id) if drv and drv.team_id else None
        driver_engine_obj = engines_by_id.get(drv.engine_id) if drv and drv.engine_id else None
        driver_sponsor_obj = (
            sponsor_by_team_season.get((driver_team_obj.id, s.id))
            if driver_team_obj else None
        )
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
