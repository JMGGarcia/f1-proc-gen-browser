from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from db.models import Driver, DriverSeasonStats, Engine, EngineSeasonStats, Season, Sponsor, Team, TeamSeasonStats
from db.session import get_db_session
from sim.flags import NATIONALITY_FLAGS
from web.templates_env import templates

router = APIRouter(prefix="/teams")


@router.get("/")
def teams_list(request: Request, db: Session = Depends(get_db_session)):
    active_teams = db.query(Team).filter_by(is_active=True).order_by(Team.name).all()

    # Latest stats per team: use max(season_id) subquery
    latest_sid_per_team = (
        db.query(
            TeamSeasonStats.team_id,
            func.max(TeamSeasonStats.season_id).label("max_sid"),
        )
        .group_by(TeamSeasonStats.team_id)
        .subquery()
    )
    latest_tss_rows = (
        db.query(TeamSeasonStats)
        .join(
            latest_sid_per_team,
            (TeamSeasonStats.team_id == latest_sid_per_team.c.team_id)
            & (TeamSeasonStats.season_id == latest_sid_per_team.c.max_sid),
        )
        .all()
    )
    latest_tss_by_team = {row.team_id: row for row in latest_tss_rows}

    # Batch-fetch all engines and sponsors referenced
    engine_ids = list({row.engine_id for row in latest_tss_rows if row.engine_id})
    sponsor_ids = list({row.sponsor_id for row in latest_tss_rows if row.sponsor_id})

    team_ids = [t.id for t in active_teams]
    owner_engine_ids = list({t.owner_engine_id for t in active_teams if t.owner_engine_id})
    owner_sponsor_ids = list({t.owner_sponsor_id for t in active_teams if t.owner_sponsor_id})
    all_engine_ids = list(set(engine_ids) | set(owner_engine_ids))
    all_sponsor_ids = list(set(sponsor_ids) | set(owner_sponsor_ids))

    engines_by_id = {e.id: e for e in db.query(Engine).filter(Engine.id.in_(all_engine_ids)).all()}
    sponsors_by_id = {s.id: s for s in db.query(Sponsor).filter(Sponsor.id.in_(all_sponsor_ids)).all()}

    for team in active_teams:
        latest = latest_tss_by_team.get(team.id)
        team.latest_stats = latest
        team.current_engine = engines_by_id.get(latest.engine_id) if latest and latest.engine_id else None
        team.current_sponsor = sponsors_by_id.get(latest.sponsor_id) if latest and latest.sponsor_id else None
        team.owner_engine_obj = engines_by_id.get(team.owner_engine_id) if team.owner_engine_id else None
        team.owner_sponsor_obj = sponsors_by_id.get(team.owner_sponsor_id) if team.owner_sponsor_id else None

    return templates.TemplateResponse(request, "teams_list.html", {
        "teams": active_teams,
    })


@router.get("/former")
def teams_former(request: Request, db: Session = Depends(get_db_session)):
    """List of inactive/former teams."""
    old_teams = db.query(Team).filter_by(is_active=False).order_by(Team.name).all()
    old_team_ids = [t.id for t in old_teams]

    # Batch-fetch successors: teams whose predecessor_team_id is in old_team_ids
    successors = db.query(Team).filter(Team.predecessor_team_id.in_(old_team_ids)).all()
    successor_by_predecessor = {t.predecessor_team_id: t for t in successors}

    for team in old_teams:
        team.flag = NATIONALITY_FLAGS.get(team.nationality, "")
        team.successor = successor_by_predecessor.get(team.id)

    return templates.TemplateResponse(request, "teams_former.html", {
        "former_teams": old_teams,
    })


@router.get("/{team_id}")
def team_detail(team_id: int, request: Request, db: Session = Depends(get_db_session)):
    team = db.query(Team).filter_by(id=team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    season_history = (
        db.query(TeamSeasonStats)
        .filter_by(team_id=team_id)
        .order_by(TeamSeasonStats.season_id)
        .all()
    )

    # Batch-fetch all referenced objects for season_history
    hist_season_ids = [e.season_id for e in season_history]
    hist_engine_ids = list({e.engine_id for e in season_history if e.engine_id})
    hist_sponsor_ids = list({e.sponsor_id for e in season_history if e.sponsor_id})

    seasons_by_id = {s.id: s for s in db.query(Season).filter(Season.id.in_(hist_season_ids)).all()}
    engines_by_id = {e.id: e for e in db.query(Engine).filter(Engine.id.in_(hist_engine_ids)).all()}
    sponsors_by_id = {s.id: s for s in db.query(Sponsor).filter(Sponsor.id.in_(hist_sponsor_ids)).all()}

    # Batch-fetch EngineSeasonStats for (engine_id, season_id) pairs
    ess_rows = (
        db.query(EngineSeasonStats)
        .filter(
            EngineSeasonStats.engine_id.in_(hist_engine_ids),
            EngineSeasonStats.season_id.in_(hist_season_ids),
        )
        .all()
    )
    ess_by_key = {(e.engine_id, e.season_id): e for e in ess_rows}

    for entry in season_history:
        entry.season_obj = seasons_by_id.get(entry.season_id)
        entry.engine_obj = engines_by_id.get(entry.engine_id) if entry.engine_id else None
        entry.sponsor_obj = sponsors_by_id.get(entry.sponsor_id) if entry.sponsor_id else None
        if entry.engine_id:
            eng_s = ess_by_key.get((entry.engine_id, entry.season_id))
            entry.engine_power = int(eng_s.power * 100) if eng_s else None
        else:
            entry.engine_power = None

    # All drivers who ever drove for this team (DISTINCT driver_id)
    driver_stints = (
        db.query(DriverSeasonStats.driver_id)
        .filter_by(team_id=team_id)
        .distinct()
        .all()
    )
    unique_driver_ids = [row.driver_id for row in driver_stints]
    unique_drivers = db.query(Driver).filter(Driver.id.in_(unique_driver_ids)).order_by(Driver.last_name).all()
    for drv in unique_drivers:
        drv.flag = NATIONALITY_FLAGS.get(drv.nationality, "")

    # Current roster: drivers in the latest completed season for this team
    latest_season = seasons_by_id.get(hist_season_ids[-1]) if hist_season_ids else None
    current_drivers = []
    if latest_season:
        current_stats = (
            db.query(DriverSeasonStats)
            .filter_by(team_id=team_id, season_id=latest_season.id)
            .all()
        )
        curr_driver_ids = [cs.driver_id for cs in current_stats]
        curr_drivers_by_id = {
            d.id: d for d in
            db.query(Driver)
            .filter(Driver.id.in_(curr_driver_ids), Driver.retired == False)
            .all()
        }
        for cs in current_stats:
            drv = curr_drivers_by_id.get(cs.driver_id)
            if drv:
                drv.flag = NATIONALITY_FLAGS.get(drv.nationality, "")
                drv.current_skill = int(cs.skill * 100)
                drv.current_age = cs.age
                current_drivers.append(drv)

    total_wins = sum(e.championship_position == 1 for e in season_history)
    latest_stats = season_history[-1] if season_history else None

    # Owner objects for the team card
    owner_engine_ids = [team.owner_engine_id] if team.owner_engine_id else []
    owner_sponsor_ids = [team.owner_sponsor_id] if team.owner_sponsor_id else []
    all_engines = {e.id: e for e in db.query(Engine).filter(Engine.id.in_(owner_engine_ids)).all()}
    all_sponsors = {s.id: s for s in db.query(Sponsor).filter(Sponsor.id.in_(owner_sponsor_ids)).all()}

    team.owner_engine_obj = all_engines.get(team.owner_engine_id) if team.owner_engine_id else None
    team.owner_sponsor_obj = all_sponsors.get(team.owner_sponsor_id) if team.owner_sponsor_id else None
    team.predecessor = (
        db.query(Team).filter_by(id=team.predecessor_team_id).first()
        if team.predecessor_team_id else None
    )

    # Compute current finance level for display
    finance_level = team.finance_base or 2
    if latest_stats and latest_stats.sponsor_id:
        current_sponsor = sponsors_by_id.get(latest_stats.sponsor_id)
        if current_sponsor:
            finance_level += {"small": 1, "medium": 2, "large": 3}.get(current_sponsor.tier, 0)
    if latest_stats and latest_stats.championship_position == 1:
        finance_level += 1  # team champion bonus
    # Check if this team had the drivers' champion last season
    if latest_stats:
        drv_champ = (
            db.query(DriverSeasonStats)
            .filter_by(season_id=latest_stats.season_id, championship_position=1, team_id=team_id)
            .first()
        )
        if drv_champ:
            finance_level += 1

    return templates.TemplateResponse(request, "team_detail.html", {
        "team": team,
        "season_history": season_history,
        "unique_drivers": unique_drivers,
        "current_drivers": current_drivers,
        "total_wins": total_wins,
        "latest_stats": latest_stats,
        "finance_level": finance_level,
        "finance_base": team.finance_base,
    })
