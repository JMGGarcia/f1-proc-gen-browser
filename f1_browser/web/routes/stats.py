from fastapi import APIRouter, Depends, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from db.models import Driver, DriverSeasonStats, Engine, Team, TeamSeasonStats
from db.session import get_db_session
from sim.flags import NATIONALITY_FLAGS

from web.templates_env import templates

router = APIRouter(prefix="/stats")


def _driver_champs(db):
    rows = (
        db.query(DriverSeasonStats.driver_id, func.count().label("wins"))
        .filter_by(championship_position=1)
        .group_by(DriverSeasonStats.driver_id)
        .order_by(func.count().desc())
        .all()
    )
    result = []
    for row in rows:
        d = db.query(Driver).filter_by(id=row.driver_id).first()
        if d:
            d.flag = NATIONALITY_FLAGS.get(d.nationality, "")
            result.append((d, row.wins))
    return result


def _team_champs_by_source(db, model, label):
    """Championship wins counted from either driver or team champion source."""
    rows = (
        db.query(model.team_id, func.count().label("wins"))
        .filter_by(championship_position=1)
        .group_by(model.team_id)
        .order_by(func.count().desc())
        .all()
    )
    result = []
    for row in rows:
        t = db.query(Team).filter_by(id=row.team_id).first()
        if t:
            result.append((t, row.wins))
    return result


def _engine_champs_by_source(db, model):
    """Engine wins from whichever season stats model is passed."""
    winning_seasons = db.query(model).filter_by(championship_position=1).all()
    counts: dict = {}
    for ts in winning_seasons:
        if ts.engine_id:
            counts[ts.engine_id] = counts.get(ts.engine_id, 0) + 1
    result = []
    for engine_id, wins in sorted(counts.items(), key=lambda x: x[1], reverse=True):
        e = db.query(Engine).filter_by(id=engine_id).first()
        if e:
            result.append((e, wins))
    return result


@router.get("/")
def stats(request: Request, db: Session = Depends(get_db_session)):
    return templates.TemplateResponse(request, "stats.html", {
        "driver_champs":          _driver_champs(db),
        "teams_by_driver_champ":  _team_champs_by_source(db, DriverSeasonStats, "driver"),
        "teams_by_team_champ":    _team_champs_by_source(db, TeamSeasonStats, "team"),
        "engines_by_driver_champ": _engine_champs_by_source(db, DriverSeasonStats),
        "engines_by_team_champ":   _engine_champs_by_source(db, TeamSeasonStats),
    })
