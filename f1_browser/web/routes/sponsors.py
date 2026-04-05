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
    for sp in sponsors:
        # Find current team via latest TeamSeasonStats
        current_team = None
        if latest_season:
            tss = db.query(TeamSeasonStats).filter_by(
                sponsor_id=sp.id, season_id=latest_season.id
            ).first()
            if tss:
                current_team = db.query(Team).filter_by(id=tss.team_id).first()
        sp.current_team = current_team

    # Group by tier for display
    large = [s for s in sponsors if s.tier == "large"]
    medium = [s for s in sponsors if s.tier == "medium"]
    small = [s for s in sponsors if s.tier == "small"]

    return templates.TemplateResponse(request, "sponsors_list.html", {
        "large": large,
        "medium": medium,
        "small": small,
    })
