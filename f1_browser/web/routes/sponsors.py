from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from db.models import Sponsor, Team, TeamSeasonStats, Season
from db.session import get_db_session
from web.routes._event_helpers import get_entity_events
from web.templates_env import templates

router = APIRouter(prefix="/sponsors")


@router.get("/")
def sponsors_list(request: Request, db: Session = Depends(get_db_session)):
    sponsors = db.query(Sponsor).order_by(Sponsor.tier, Sponsor.name).all()
    latest_season = (
        db.query(Season).filter_by(completed=True).order_by(Season.number.desc()).first()
    )

    if latest_season:
        sponsor_ids = [sp.id for sp in sponsors]
        tss_list = (
            db.query(TeamSeasonStats)
            .filter(
                TeamSeasonStats.sponsor_id.in_(sponsor_ids),
                TeamSeasonStats.season_id == latest_season.id,
            )
            .all()
        )
        team_ids = [tss.team_id for tss in tss_list]
        teams_by_id = {t.id: t for t in db.query(Team).filter(Team.id.in_(team_ids)).all()}
        team_by_sponsor = {tss.sponsor_id: teams_by_id.get(tss.team_id) for tss in tss_list}
    else:
        team_by_sponsor = {}

    for sp in sponsors:
        sp.current_team = team_by_sponsor.get(sp.id)

    # Group by tier for display
    large = [s for s in sponsors if s.tier == "large"]
    medium = [s for s in sponsors if s.tier == "medium"]
    small = [s for s in sponsors if s.tier == "small"]

    return templates.TemplateResponse(request, "sponsors_list.html", {
        "large": large,
        "medium": medium,
        "small": small,
    })


@router.get("/{sponsor_id}")
def sponsor_detail(sponsor_id: int, request: Request, db: Session = Depends(get_db_session), events_page: int = 1):
    sponsor = db.query(Sponsor).filter_by(id=sponsor_id).first()
    if not sponsor:
        raise HTTPException(status_code=404, detail="Sponsor not found")

    # All season stats for this sponsor, newest first
    history_rows = (
        db.query(TeamSeasonStats)
        .filter_by(sponsor_id=sponsor_id)
        .order_by(TeamSeasonStats.season_id.desc())
        .all()
    )
    season_ids = [r.season_id for r in history_rows]
    team_ids = list({r.team_id for r in history_rows if r.team_id})
    seasons_by_id = {s.id: s for s in db.query(Season).filter(Season.id.in_(season_ids)).all()}
    teams_by_id = {t.id: t for t in db.query(Team).filter(Team.id.in_(team_ids)).all()}

    for row in history_rows:
        row.season_obj = seasons_by_id.get(row.season_id)
        row.team_obj = teams_by_id.get(row.team_id) if row.team_id else None

    events, ep, total_pages = get_entity_events(db, "sponsor", sponsor_id, events_page)

    return templates.TemplateResponse(request, "sponsor_detail.html", {
        "sponsor": sponsor,
        "history": history_rows,
        "events": events,
        "events_page": ep,
        "events_total_pages": total_pages,
    })
