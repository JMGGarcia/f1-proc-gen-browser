import asyncio
import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from db.models import Driver, DriverSeasonStats, Engine, Race, RaceResult, Season, Sponsor, Team, TeamSeasonStats, WorldEvent
from db.session import get_db_session, get_session
from web import broadcaster, sim_state
from web.templates_env import templates

router = APIRouter()


@router.get("/")
def index(request: Request, db: Session = Depends(get_db_session)):
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
        "summaries": summaries,
        "total_seasons": total_seasons,
        "sim_available": sim_state.is_available(),
        "sim_busy": sim_state.is_busy(),
        "sim_tick_running": sim_state.is_tick_running(),
    })


@router.get("/live-feed")
async def live_feed(request: Request):
    async def event_stream():
        q = broadcaster.subscribe()
        try:
            snap = _build_snapshot()
            yield f"data: {json.dumps(snap)}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"data: {data}\n\n"
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            broadcaster.unsubscribe(q)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _build_snapshot() -> dict:
    """Return current standings for a freshly-connected SSE client."""
    with get_session() as db:
        season = db.query(Season).order_by(Season.number.desc()).first()
        if season is None:
            return {"type": "snapshot", "season": None, "driver_standings": [], "team_standings": []}

        season_id = season.id
        season_num = season.number

        # Latest completed round in this season
        latest_race = (
            db.query(Race)
            .filter_by(season_id=season_id)
            .order_by(Race.round_number.desc())
            .first()
        )
        round_completed = latest_race.round_number if latest_race else 0

        # Use DriverSeasonStats if season is complete, else aggregate from race results
        if season.completed:
            driver_stats = (
                db.query(DriverSeasonStats)
                .filter_by(season_id=season_id)
                .order_by(DriverSeasonStats.championship_position)
                .limit(20)
                .all()
            )
            driver_ids = [s.driver_id for s in driver_stats]
            team_ids = [s.team_id for s in driver_stats if s.team_id]
            drivers_map = {d.id: d for d in db.query(Driver).filter(Driver.id.in_(driver_ids)).all()}
            teams_map = {t.id: t for t in db.query(Team).filter(Team.id.in_(team_ids)).all()}
            driver_standings = [
                {
                    "pos": s.championship_position,
                    "driver_name": f"{drivers_map[s.driver_id].first_name} {drivers_map[s.driver_id].last_name}"
                    if s.driver_id in drivers_map else "—",
                    "team_name": teams_map[s.team_id].name if s.team_id and s.team_id in teams_map else "—",
                    "points": s.total_points,
                }
                for s in driver_stats
            ]
            team_stats = (
                db.query(TeamSeasonStats)
                .filter_by(season_id=season_id)
                .order_by(TeamSeasonStats.championship_position)
                .limit(20)
                .all()
            )
            team_ids2 = [s.team_id for s in team_stats]
            teams_map2 = {t.id: t for t in db.query(Team).filter(Team.id.in_(team_ids2)).all()}
            team_standings = [
                {
                    "pos": s.championship_position,
                    "team_name": teams_map2[s.team_id].name if s.team_id in teams_map2 else "—",
                    "color_primary": teams_map2[s.team_id].color_primary if s.team_id in teams_map2 else "#333",
                    "color_secondary": teams_map2[s.team_id].color_secondary if s.team_id in teams_map2 else "#fff",
                    "points": s.total_points,
                }
                for s in team_stats
            ]
        else:
            # In-progress season: aggregate from race results
            from sqlalchemy import func
            driver_pts = (
                db.query(
                    Driver.id,
                    Driver.first_name,
                    Driver.last_name,
                    Team.name.label("team_name"),
                    func.sum(RaceResult.points).label("pts"),
                )
                .join(RaceResult, RaceResult.driver_id == Driver.id)
                .join(Race, Race.id == RaceResult.race_id)
                .join(Team, Team.id == RaceResult.team_id)
                .filter(Race.season_id == season_id)
                .group_by(Driver.id, Team.id)
                .order_by(func.sum(RaceResult.points).desc())
                .limit(20)
                .all()
            )
            driver_standings = [
                {"pos": i + 1, "driver_name": f"{r.first_name} {r.last_name}", "team_name": r.team_name, "points": r.pts or 0}
                for i, r in enumerate(driver_pts)
            ]
            team_pts = (
                db.query(
                    Team.id,
                    Team.name,
                    Team.color_primary,
                    Team.color_secondary,
                    func.sum(RaceResult.points).label("pts"),
                )
                .join(RaceResult, RaceResult.team_id == Team.id)
                .join(Race, Race.id == RaceResult.race_id)
                .filter(Race.season_id == season_id)
                .group_by(Team.id)
                .order_by(func.sum(RaceResult.points).desc())
                .limit(20)
                .all()
            )
            team_standings = [
                {"pos": i + 1, "team_name": r.name, "color_primary": r.color_primary, "color_secondary": r.color_secondary, "points": r.pts or 0}
                for i, r in enumerate(team_pts)
            ]

        return {
            "type": "snapshot",
            "season": season_num,
            "round_completed": round_completed,
            "driver_standings": driver_standings,
            "team_standings": team_standings,
        }
