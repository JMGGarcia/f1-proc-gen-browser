from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from db.models import Engine, EngineSeasonStats, Season, Team, TeamSeasonStats
from db.session import get_db_session

from web.templates_env import templates

router = APIRouter(prefix="/engines")


@router.get("/")
def engines_list(request: Request, db: Session = Depends(get_db_session)):
    engines = db.query(Engine).order_by(Engine.name).all()

    # Championship wins per engine (one query)
    win_rows = (
        db.query(TeamSeasonStats.engine_id, func.count().label("wins"))
        .filter_by(championship_position=1)
        .group_by(TeamSeasonStats.engine_id)
        .all()
    )
    wins_by_engine = {row.engine_id: row.wins for row in win_rows}

    # Latest season stats per engine (one query — max season_id per engine)
    latest_season_per_engine = (
        db.query(
            EngineSeasonStats.engine_id,
            func.max(EngineSeasonStats.season_id).label("max_sid"),
        )
        .group_by(EngineSeasonStats.engine_id)
        .subquery()
    )
    latest_ess_rows = (
        db.query(EngineSeasonStats)
        .join(
            latest_season_per_engine,
            (EngineSeasonStats.engine_id == latest_season_per_engine.c.engine_id)
            & (EngineSeasonStats.season_id == latest_season_per_engine.c.max_sid),
        )
        .all()
    )
    latest_ess_by_engine = {row.engine_id: row for row in latest_ess_rows}

    for engine in engines:
        engine.championship_wins = wins_by_engine.get(engine.id, 0)
        latest = latest_ess_by_engine.get(engine.id)
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

    # Batch-fetch all seasons referenced
    season_ids = [e.season_id for e in season_stats]
    seasons_by_id = {s.id: s for s in db.query(Season).filter(Season.id.in_(season_ids)).all()}

    # Batch-fetch all TeamSeasonStats for this engine across its seasons
    all_tss = (
        db.query(TeamSeasonStats)
        .filter(
            TeamSeasonStats.engine_id == engine_id,
            TeamSeasonStats.season_id.in_(season_ids),
        )
        .all()
    )
    team_ids = list({tss.team_id for tss in all_tss})
    teams_by_id = {t.id: t for t in db.query(Team).filter(Team.id.in_(team_ids)).all()}

    # Build lookups: season_id -> [teams], season_id -> won_championship
    teams_by_season: dict[int, list] = {}
    champ_seasons: set[int] = set()
    for tss in all_tss:
        teams_by_season.setdefault(tss.season_id, [])
        t = teams_by_id.get(tss.team_id)
        if t:
            teams_by_season[tss.season_id].append(t)
        if tss.championship_position == 1:
            champ_seasons.add(tss.season_id)

    for entry in season_stats:
        entry.season_obj = seasons_by_id.get(entry.season_id)
        entry.teams = teams_by_season.get(entry.season_id, [])

    championship_wins = len(champ_seasons)

    return templates.TemplateResponse(request, "engine_detail.html", {
        "engine": engine,
        "season_stats": season_stats,
        "championship_wins": championship_wins,
    })
