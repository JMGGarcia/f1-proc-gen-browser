from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from db.models import Driver, DriverModifier, DriverSeasonStats, Engine, Race, RaceResult, Season, Team, Track
from db.session import get_db_session
from sim.driver_events import get_event_def_by_id
from sim.flags import NATIONALITY_FLAGS
from web import sim_state
from web.routes._event_helpers import get_entity_events
from web.templates_env import templates

router = APIRouter(prefix="/drivers")


@router.get("/")
def drivers_list(request: Request, db: Session = Depends(get_db_session)):
    drivers = db.query(Driver).filter_by(retired=False).order_by(Driver.last_name).all()
    driver_ids = [d.id for d in drivers]

    # Prefer in-memory runner state: it reflects all off-season signings including
    # transfers that happened after the last completed season but before any race
    # results for the new season exist.
    runner = sim_state.get_runner()
    team_db_id_by_driver_id: dict[int, int] = {}
    if runner is not None:
        try:
            team_db_id_by_driver_id = {
                drv.db_id: drv.team.db_id
                for drv in runner.drivers
                if drv.team is not None and drv.team.db_id
            }
        except Exception:
            pass  # fall through to DSS fallback

    if not team_db_id_by_driver_id:
        # Fallback: use only the latest completed season DSS.
        # May miss transfers that happened after that season's off-season.
        latest_season = (
            db.query(Season).filter_by(completed=True).order_by(Season.number.desc()).first()
        )
        latest_season_id = latest_season.id if latest_season else None
        if latest_season_id:
            fallback_rows = (
                db.query(DriverSeasonStats)
                .filter(
                    DriverSeasonStats.driver_id.in_(driver_ids),
                    DriverSeasonStats.season_id == latest_season_id,
                )
                .all()
            )
            team_db_id_by_driver_id = {
                row.driver_id: row.team_id for row in fallback_rows if row.team_id
            }

    team_ids = list(set(team_db_id_by_driver_id.values()))
    teams_by_id = {t.id: t for t in db.query(Team).filter(Team.id.in_(team_ids)).all()}

    for d in drivers:
        db_team_id = team_db_id_by_driver_id.get(d.id)
        d.current_team = teams_by_id.get(db_team_id) if db_team_id else None
        d.display_age = d.age or "—"
        d.display_skill = int(d.skill * 100) if d.skill is not None else "—"
        d.flag = NATIONALITY_FLAGS.get(d.nationality, "")

    drivers_with_team = [d for d in drivers if d.current_team]
    drivers_without_team = [d for d in drivers if not d.current_team]

    return templates.TemplateResponse(request, "drivers_list.html", {
        "drivers_with_team": drivers_with_team,
        "drivers_without_team": drivers_without_team,
    })


@router.get("/retired")
def drivers_retired(request: Request, db: Session = Depends(get_db_session)):
    drivers = db.query(Driver).filter_by(retired=True).order_by(Driver.last_name).all()
    driver_ids = [d.id for d in drivers]

    # Latest season stats per retired driver (one subquery)
    latest_sid_per_driver = (
        db.query(
            DriverSeasonStats.driver_id,
            func.max(DriverSeasonStats.season_id).label("max_sid"),
        )
        .filter(DriverSeasonStats.driver_id.in_(driver_ids))
        .group_by(DriverSeasonStats.driver_id)
        .subquery()
    )
    latest_dss_rows = (
        db.query(DriverSeasonStats)
        .join(
            latest_sid_per_driver,
            (DriverSeasonStats.driver_id == latest_sid_per_driver.c.driver_id)
            & (DriverSeasonStats.season_id == latest_sid_per_driver.c.max_sid),
        )
        .all()
    )
    latest_dss_by_driver = {row.driver_id: row for row in latest_dss_rows}

    for d in drivers:
        d.last_stats = latest_dss_by_driver.get(d.id)
        d.flag = NATIONALITY_FLAGS.get(d.nationality, "")

    return templates.TemplateResponse(request, "drivers_retired.html", {
        "retired_drivers": drivers,
    })


@router.get("/{driver_id}")
def driver_detail(driver_id: int, request: Request, db: Session = Depends(get_db_session), events_page: int = 1):
    driver = db.query(Driver).filter_by(id=driver_id).first()
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    driver.flag = NATIONALITY_FLAGS.get(driver.nationality, "")

    career = (
        db.query(DriverSeasonStats)
        .filter_by(driver_id=driver_id)
        .order_by(DriverSeasonStats.season_id)
        .all()
    )

    # Batch-fetch all referenced objects for career
    career_season_ids = [e.season_id for e in career]
    career_team_ids = list({e.team_id for e in career if e.team_id})
    career_engine_ids = list({e.engine_id for e in career if e.engine_id})
    seasons_by_id = {s.id: s for s in db.query(Season).filter(Season.id.in_(career_season_ids)).all()}
    career_teams = {t.id: t for t in db.query(Team).filter(Team.id.in_(career_team_ids)).all()}
    career_engines = {e.id: e for e in db.query(Engine).filter(Engine.id.in_(career_engine_ids)).all()}

    for entry in career:
        entry.season_obj = seasons_by_id.get(entry.season_id)
        entry.team_obj = career_teams.get(entry.team_id) if entry.team_id else None
        entry.engine_obj = career_engines.get(entry.engine_id) if entry.engine_id else None

    total_wins = db.query(RaceResult).filter_by(driver_id=driver_id, position=1).count()
    total_podiums = (
        db.query(RaceResult)
        .filter(RaceResult.driver_id == driver_id, RaceResult.position <= 3, RaceResult.dnf == False)
        .count()
    )

    # Circuit preferences
    liked_track_names = []
    disliked_track_names = []
    if driver.liked_tracks:
        ids = [int(x) for x in driver.liked_tracks.split(",") if x.strip()]
        tracks = db.query(Track).filter(Track.id.in_(ids)).all()
        liked_track_names = [t.name for t in tracks]
    if driver.disliked_tracks:
        ids = [int(x) for x in driver.disliked_tracks.split(",") if x.strip()]
        tracks = db.query(Track).filter(Track.id.in_(ids)).all()
        disliked_track_names = [t.name for t in tracks]

    events, ep, total_pages = get_entity_events(db, "driver", driver_id, events_page)

    # Query modifiers — active for living drivers, all (active + inactive) for retired
    modifiers_query = db.query(DriverModifier).filter_by(driver_id=driver_id)
    if not driver.retired:
        modifiers_query = modifiers_query.filter_by(active=True)
    modifiers = modifiers_query.order_by(DriverModifier.applied_season.desc()).all()

    # Parse modifier_json for display
    import json as _json
    driver_full_name = f"{driver.first_name} {driver.last_name}"
    for mod in modifiers:
        try:
            mod.parsed_modifiers = _json.loads(mod.modifier_json) if isinstance(mod.modifier_json, str) else mod.modifier_json
        except Exception:
            mod.parsed_modifiers = {}
        defn = get_event_def_by_id(mod.event_def_id) or {}
        desc_template = defn.get("description", "")
        mod.display_description = desc_template.replace("{driver}", driver_full_name) if desc_template else ""

    return templates.TemplateResponse(request, "driver_detail.html", {
        "driver": driver,
        "career": career,
        "total_wins": total_wins,
        "total_podiums": total_podiums,
        "liked_track_names": liked_track_names,
        "disliked_track_names": disliked_track_names,
        "events": events,
        "events_page": ep,
        "events_total_pages": total_pages,
        "modifiers": modifiers,
    })
