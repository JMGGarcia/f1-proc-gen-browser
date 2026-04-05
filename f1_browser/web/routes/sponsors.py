from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from db.models import Sponsor, Team, TeamSeasonStats, Season
from db.session import get_db_session
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
