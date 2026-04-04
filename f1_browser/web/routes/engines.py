from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from db.models import Engine, EngineSeasonStats, Season, Team, TeamSeasonStats
from db.session import get_db_session

from web.templates_env import templates

router = APIRouter(prefix="/engines")


@router.get("/")
def engines_list(request: Request, db: Session = Depends(get_db_session)):
    engines = db.query(Engine).order_by(Engine.name).all()
    for engine in engines:
        # Championship wins = seasons where the champion team used this engine
        wins = (
            db.query(TeamSeasonStats)
            .filter_by(engine_id=engine.id, championship_position=1)
            .count()
        )
        engine.championship_wins = wins
        latest = (
            db.query(EngineSeasonStats)
            .filter_by(engine_id=engine.id)
            .order_by(EngineSeasonStats.season_id.desc())
            .first()
        )
        engine.latest_power = int(latest.power * 100) if latest else None
        engine.latest_reliability = int(latest.reliability * 100) if latest else None

    return templates.TemplateResponse(request, "engines_list.html", {
        "engines": engines,
    })


@router.get("/{engine_id}")
def engine_detail(engine_id: int, request: Request, db: Session = Depends(get_db_session)):
    engine = db.query(Engine).filter_by(id=engine_id).first()
    if not engine:
        raise HTTPException(status_code=404, detail="Engine not found")

    season_stats = (
        db.query(EngineSeasonStats)
        .filter_by(engine_id=engine_id)
        .order_by(EngineSeasonStats.season_id)
        .all()
    )
    for entry in season_stats:
        entry.season_obj = db.query(Season).filter_by(id=entry.season_id).first()
        # Teams using this engine that season
        team_stints = (
            db.query(TeamSeasonStats)
            .filter_by(engine_id=engine_id, season_id=entry.season_id)
            .all()
        )
        entry.teams = [
            db.query(Team).filter_by(id=ts.team_id).first()
            for ts in team_stints
        ]
        entry.teams = [t for t in entry.teams if t]  # drop None

    championship_wins = sum(
        1 for e in season_stats
        if db.query(TeamSeasonStats)
           .filter_by(engine_id=engine_id, season_id=e.season_id, championship_position=1)
           .first() is not None
    )

    return templates.TemplateResponse(request, "engine_detail.html", {
        "engine": engine,
        "season_stats": season_stats,
        "championship_wins": championship_wins,
    })
