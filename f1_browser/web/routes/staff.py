from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from db.models import Season, Team, TeamChief, TeamSeasonStats
from db.session import get_db_session
from sim.flags import NATIONALITY_FLAGS
from web.templates_env import templates

router = APIRouter(prefix="/staff")

ROLE_DISPLAY = {"owner": "Owner", "cto": "CTO", "cmo": "CMO", "cpo": "CPO"}

_ROLE_SKILL_LABELS = {
    "owner": ("Vision", None),
    "cto": ("Dev", "Eng Scout"),
    "cmo": (None, None),
    "cpo": ("Scouting", None),
}


@router.get("/")
def staff_list(request: Request, db: Session = Depends(get_db_session)):
    chiefs = (
        db.query(TeamChief)
        .filter_by(retired=False)
        .order_by(TeamChief.last_name)
        .all()
    )

    team_ids = list({c.team_id for c in chiefs if c.team_id})
    teams_by_id = {t.id: t for t in db.query(Team).filter(Team.id.in_(team_ids)).all()}

    for c in chiefs:
        c.flag = NATIONALITY_FLAGS.get(c.nationality, "")
        c.current_team = teams_by_id.get(c.team_id) if c.team_id else None
        c.role_display = ROLE_DISPLAY.get(c.role, c.role)

    on_team = [c for c in chiefs if c.current_team]
    free_agents = [c for c in chiefs if not c.current_team]

    return templates.TemplateResponse(request, "staff_list.html", {
        "on_team": on_team,
        "free_agents": free_agents,
    })


@router.get("/retired")
def staff_retired(request: Request, db: Session = Depends(get_db_session)):
    chiefs = (
        db.query(TeamChief)
        .filter_by(retired=True)
        .order_by(TeamChief.retired_season.desc(), TeamChief.last_name)
        .all()
    )

    for c in chiefs:
        c.flag = NATIONALITY_FLAGS.get(c.nationality, "")
        c.role_display = ROLE_DISPLAY.get(c.role, c.role)

    return templates.TemplateResponse(request, "staff_retired.html", {
        "retired_chiefs": chiefs,
    })


@router.get("/{chief_id}")
def staff_detail(chief_id: int, request: Request, db: Session = Depends(get_db_session)):
    chief = db.query(TeamChief).filter_by(id=chief_id).first()
    if not chief:
        raise HTTPException(status_code=404, detail="Staff member not found")

    chief.flag = NATIONALITY_FLAGS.get(chief.nationality, "")
    chief.role_display = ROLE_DISPLAY.get(chief.role, chief.role)
    chief.skill_label1, chief.skill_label2 = _ROLE_SKILL_LABELS.get(chief.role, (None, None))

    role_col_map = {
        "owner": TeamSeasonStats.owner_chief_id,
        "cto": TeamSeasonStats.cto_chief_id,
        "cmo": TeamSeasonStats.cmo_chief_id,
        "cpo": TeamSeasonStats.cpo_chief_id,
    }
    col = role_col_map.get(chief.role)
    career_rows = []
    if col is not None:
        career_rows = (
            db.query(TeamSeasonStats)
            .filter(col == chief_id)
            .order_by(TeamSeasonStats.season_id)
            .all()
        )

    season_ids = [r.season_id for r in career_rows]
    team_ids = list({r.team_id for r in career_rows if r.team_id})
    seasons_by_id = {s.id: s for s in db.query(Season).filter(Season.id.in_(season_ids)).all()}
    teams_by_id = {t.id: t for t in db.query(Team).filter(Team.id.in_(team_ids)).all()}

    career = []
    for row in career_rows:
        if chief.role == "owner":
            skill1, skill2 = row.owner_skill, None
        elif chief.role == "cto":
            skill1, skill2 = row.cto_development, row.cto_eng_scouting
        elif chief.role == "cpo":
            skill1, skill2 = row.cpo_scouting, None
        else:
            skill1, skill2 = None, None

        career.append({
            "season": seasons_by_id.get(row.season_id),
            "team": teams_by_id.get(row.team_id) if row.team_id else None,
            "skill1": skill1,
            "skill2": skill2,
            "champ_pos": row.championship_position,
            "points": row.total_points,
        })

    return templates.TemplateResponse(request, "staff_detail.html", {
        "chief": chief,
        "career": career,
    })
