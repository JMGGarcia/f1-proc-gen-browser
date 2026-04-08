from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from db.models import (
    Driver, DriverSeasonStats, Engine, EngineSeasonStats, Race, RaceResult,
    Season, Sponsor, Team, TeamSeasonStats, Track,
)
from db.session import get_db_session
from sim.flags import NATIONALITY_FLAGS
from web import sim_state
from web.templates_env import templates

router = APIRouter(prefix="/seasons")


@router.get("/")
def seasons_list(request: Request, db: Session = Depends(get_db_session)):
    seasons = (
        db.query(Season)
        .filter_by(completed=True)
        .order_by(Season.number.desc())  # latest first
        .all()
    )
    season_ids = [s.id for s in seasons]

    # Batch-fetch championship stats for all seasons
    drv_champ_list = (
        db.query(DriverSeasonStats)
        .filter(
            DriverSeasonStats.season_id.in_(season_ids),
            DriverSeasonStats.championship_position == 1,
        )
        .all()
    )
    drv_champ_by_season = {ds.season_id: ds for ds in drv_champ_list}

    tm_champ_list = (
        db.query(TeamSeasonStats)
        .filter(
            TeamSeasonStats.season_id.in_(season_ids),
            TeamSeasonStats.championship_position == 1,
        )
        .all()
    )
    tm_champ_by_season = {ts.season_id: ts for ts in tm_champ_list}

    # Collect all IDs needed
    driver_ids = list({ds.driver_id for ds in drv_champ_list if ds.driver_id})
    drv_team_ids = list({ds.team_id for ds in drv_champ_list if ds.team_id})
    drv_engine_ids = list({ds.engine_id for ds in drv_champ_list if ds.engine_id})
    tm_team_ids = list({ts.team_id for ts in tm_champ_list if ts.team_id})
    all_team_ids = list(set(drv_team_ids) | set(tm_team_ids))

    drivers_by_id = {d.id: d for d in db.query(Driver).filter(Driver.id.in_(driver_ids)).all()}
    teams_by_id = {t.id: t for t in db.query(Team).filter(Team.id.in_(all_team_ids)).all()}
    engines_by_id = {e.id: e for e in db.query(Engine).filter(Engine.id.in_(drv_engine_ids)).all()}

    # Engine power: batch-fetch EngineSeasonStats for (engine_id, season_id) pairs
    ess_rows = (
        db.query(EngineSeasonStats)
        .filter(
            EngineSeasonStats.engine_id.in_(drv_engine_ids),
            EngineSeasonStats.season_id.in_(season_ids),
        )
        .all()
    )
    ess_by_key = {(e.engine_id, e.season_id): e for e in ess_rows}

    # Sponsor: batch-fetch TeamSeasonStats for drv_team_ids to get sponsor_id
    drv_tss_rows = (
        db.query(TeamSeasonStats)
        .filter(
            TeamSeasonStats.team_id.in_(drv_team_ids),
            TeamSeasonStats.season_id.in_(season_ids),
        )
        .all()
    )
    sponsor_ids = list({tss.sponsor_id for tss in drv_tss_rows if tss.sponsor_id})
    sponsors_by_id = {s.id: s for s in db.query(Sponsor).filter(Sponsor.id.in_(sponsor_ids)).all()}
    sponsor_by_team_season = {
        (tss.team_id, tss.season_id): sponsors_by_id.get(tss.sponsor_id)
        for tss in drv_tss_rows
        if tss.sponsor_id
    }

    summaries = []
    for s in seasons:
        drv_stats = drv_champ_by_season.get(s.id)
        tm_stats = tm_champ_by_season.get(s.id)
        driver_obj = drivers_by_id.get(drv_stats.driver_id) if drv_stats else None
        drv_team_obj = teams_by_id.get(drv_stats.team_id) if drv_stats and drv_stats.team_id else None
        drv_engine_obj = engines_by_id.get(drv_stats.engine_id) if drv_stats and drv_stats.engine_id else None
        team_obj = teams_by_id.get(tm_stats.team_id) if tm_stats else None

        drv_engine_power = None
        if drv_stats and drv_stats.engine_id:
            eng_s = ess_by_key.get((drv_stats.engine_id, s.id))
            if eng_s:
                drv_engine_power = int(eng_s.power * 100)

        drv_sponsor_obj = (
            sponsor_by_team_season.get((drv_team_obj.id, s.id))
            if drv_team_obj else None
        )

        summaries.append({
            "season": s,
            "driver": driver_obj,
            "driver_flag": NATIONALITY_FLAGS.get(driver_obj.nationality, "") if driver_obj else "",
            "driver_skill": int(drv_stats.skill * 100) if drv_stats else None,
            "driver_team": drv_team_obj,
            "driver_engine": drv_engine_obj,
            "driver_engine_power": drv_engine_power,
            "driver_sponsor": drv_sponsor_obj,
            "team": team_obj,
        })

    return templates.TemplateResponse(request, "seasons_list.html", {
        "summaries": summaries,
    })


@router.get("/{season_num}")
def season_detail(season_num: int, request: Request, db: Session = Depends(get_db_session)):
    season = db.query(Season).filter_by(number=season_num).first()
    if not season:
        raise HTTPException(status_code=404, detail="Season not found")

    driver_stats = (
        db.query(DriverSeasonStats)
        .filter_by(season_id=season.id)
        .order_by(DriverSeasonStats.championship_position)
        .all()
    )
    team_stats = (
        db.query(TeamSeasonStats)
        .filter_by(season_id=season.id)
        .order_by(TeamSeasonStats.championship_position)
        .all()
    )
    races = (
        db.query(Race)
        .filter_by(season_id=season.id)
        .order_by(Race.round_number)
        .all()
    )

    # Batch-fetch for driver_stats
    ds_driver_ids = list({ds.driver_id for ds in driver_stats})
    ds_team_ids = list({ds.team_id for ds in driver_stats if ds.team_id})
    ds_engine_ids = list({ds.engine_id for ds in driver_stats if ds.engine_id})
    ds_drivers = {d.id: d for d in db.query(Driver).filter(Driver.id.in_(ds_driver_ids)).all()}
    ds_teams = {t.id: t for t in db.query(Team).filter(Team.id.in_(ds_team_ids)).all()}
    ds_engines = {e.id: e for e in db.query(Engine).filter(Engine.id.in_(ds_engine_ids)).all()}

    for ds in driver_stats:
        ds.driver_obj = ds_drivers.get(ds.driver_id)
        ds.team_obj = ds_teams.get(ds.team_id) if ds.team_id else None
        ds.engine_obj = ds_engines.get(ds.engine_id) if ds.engine_id else None
        ds.driver_flag = NATIONALITY_FLAGS.get(ds.driver_obj.nationality, "") if ds.driver_obj else ""

    # Batch-fetch for team_stats
    ts_team_ids = list({ts.team_id for ts in team_stats})
    ts_engine_ids = list({ts.engine_id for ts in team_stats if ts.engine_id})
    ts_teams = {t.id: t for t in db.query(Team).filter(Team.id.in_(ts_team_ids)).all()}
    ts_engines = {e.id: e for e in db.query(Engine).filter(Engine.id.in_(ts_engine_ids)).all()}
    ess_rows = (
        db.query(EngineSeasonStats)
        .filter(
            EngineSeasonStats.engine_id.in_(ts_engine_ids),
            EngineSeasonStats.season_id == season.id,
        )
        .all()
    )
    ess_by_engine = {e.engine_id: e for e in ess_rows}

    for ts in team_stats:
        ts.team_obj = ts_teams.get(ts.team_id)
        ts.engine_obj = ts_engines.get(ts.engine_id) if ts.engine_id else None
        if ts.engine_id:
            eng_s = ess_by_engine.get(ts.engine_id)
            ts.engine_power = int(eng_s.power * 100) if eng_s else None
        else:
            ts.engine_power = None

    # Batch-fetch race winners
    race_ids = [r.id for r in races]
    winner_results = (
        db.query(RaceResult)
        .filter(RaceResult.race_id.in_(race_ids), RaceResult.position == 1)
        .all()
    )
    winner_result_by_race = {wr.race_id: wr for wr in winner_results}
    winner_driver_ids = list({wr.driver_id for wr in winner_results if wr.driver_id})
    winner_team_ids = list({wr.team_id for wr in winner_results if wr.team_id})
    winner_drivers = {d.id: d for d in db.query(Driver).filter(Driver.id.in_(winner_driver_ids)).all()}
    winner_teams = {t.id: t for t in db.query(Team).filter(Team.id.in_(winner_team_ids)).all()}

    for race in races:
        wr = winner_result_by_race.get(race.id)
        race.winner = winner_drivers.get(wr.driver_id) if wr else None
        race.winner_team = winner_teams.get(wr.team_id) if wr else None

    prev_season = season_num - 1 if season_num > 1 else None
    next_season = season_num + 1 if db.query(Season).filter_by(number=season_num + 1, completed=True).first() else None

    return templates.TemplateResponse(request, "season_detail.html", {
        "season": season,
        "driver_stats": driver_stats,
        "team_stats": team_stats,
        "races": races,
        "prev_season": prev_season,
        "next_season": next_season,
    })


@router.get("/{season_num}/races/{round_num}")
def race_detail(season_num: int, round_num: int, request: Request, db: Session = Depends(get_db_session)):
    season = db.query(Season).filter_by(number=season_num).first()
    if not season:
        raise HTTPException(status_code=404, detail="Season not found")

    race = db.query(Race).filter_by(season_id=season.id, round_number=round_num).first()
    if not race:
        raise HTTPException(status_code=404, detail="Race not found")

    track = db.query(Track).filter_by(id=race.track_id).first()
    results = (
        db.query(RaceResult)
        .filter_by(race_id=race.id)
        .order_by(RaceResult.dnf, RaceResult.position)
        .all()
    )

    # Batch-fetch driver/team/engine for race results
    r_driver_ids = list({r.driver_id for r in results})
    r_team_ids = list({r.team_id for r in results})
    r_engine_ids = list({r.engine_id for r in results})
    r_drivers = {d.id: d for d in db.query(Driver).filter(Driver.id.in_(r_driver_ids)).all()}
    r_teams = {t.id: t for t in db.query(Team).filter(Team.id.in_(r_team_ids)).all()}
    r_engines = {e.id: e for e in db.query(Engine).filter(Engine.id.in_(r_engine_ids)).all()}

    # Compute gap to leader from stored total_time
    leader_time = next((r.total_time for r in results if not r.dnf and r.total_time), None)
    for r in results:
        r.driver_obj = r_drivers.get(r.driver_id)
        r.team_obj = r_teams.get(r.team_id)
        r.engine_obj = r_engines.get(r.engine_id)
        if r.dnf or r.total_time is None or leader_time is None:
            r.gap = None
        elif r.position == 1:
            r.gap = "LEADER"
        else:
            r.gap = f"+{r.total_time - leader_time:.3f}"

    prev_round = round_num - 1 if round_num > 1 else None
    next_round = (
        round_num + 1
        if db.query(Race).filter_by(season_id=season.id, round_number=round_num + 1).first()
        else None
    )

    # Check if this race is currently live — redirect to home page if so
    runner = sim_state.get_runner()
    live_state = runner.get_live_race_state() if runner else None
    is_live = (
        live_state is not None
        and live_state.get("active")
        and live_state.get("season") == season_num
        and live_state.get("round") == round_num
    )
    if is_live:
        return RedirectResponse("/", status_code=302)

    return templates.TemplateResponse(request, "race_detail.html", {
        "season": season,
        "race": race,
        "track": track,
        "results": results,
        "prev_round": prev_round,
        "next_round": next_round,
    })
