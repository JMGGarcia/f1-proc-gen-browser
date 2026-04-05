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
    driver_ids = [row.driver_id for row in rows]
    drivers_by_id = {d.id: d for d in db.query(Driver).filter(Driver.id.in_(driver_ids)).all()}
    result = []
    for row in rows:
        d = drivers_by_id.get(row.driver_id)
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
    team_ids = [row.team_id for row in rows]
    teams_by_id = {t.id: t for t in db.query(Team).filter(Team.id.in_(team_ids)).all()}
    result = []
    for row in rows:
        t = teams_by_id.get(row.team_id)
        if t:
            result.append((t, row.wins))
    return result


def _engine_champs_by_source(db, model):
    """Engine wins from whichever season stats model is passed."""
    rows = (
        db.query(model.engine_id, func.count().label("wins"))
        .filter(model.championship_position == 1, model.engine_id.isnot(None))
        .group_by(model.engine_id)
        .order_by(func.count().desc())
        .all()
    )
    engine_ids = [row.engine_id for row in rows]
    engines_by_id = {e.id: e for e in db.query(Engine).filter(Engine.id.in_(engine_ids)).all()}
    result = []
    for row in rows:
        e = engines_by_id.get(row.engine_id)
        if e:
            result.append((e, row.wins))
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
