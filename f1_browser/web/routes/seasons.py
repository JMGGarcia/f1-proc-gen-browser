from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from db.models import (
    Driver, DriverSeasonStats, Engine, EngineSeasonStats, Race, RaceResult,
    Season, Team, TeamSeasonStats, Track,
)
from db.session import get_db_session
from sim.flags import NATIONALITY_FLAGS
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
    summaries = []
    for s in seasons:
        drv_stats = db.query(DriverSeasonStats).filter_by(season_id=s.id, championship_position=1).first()
        tm_stats = db.query(TeamSeasonStats).filter_by(season_id=s.id, championship_position=1).first()
        from db.models import Sponsor
        driver_obj = db.query(Driver).filter_by(id=drv_stats.driver_id).first() if drv_stats else None
        # Driver champion's own team/engine/sponsor
        drv_team_obj = db.query(Team).filter_by(id=drv_stats.team_id).first() if (drv_stats and drv_stats.team_id) else None
        drv_engine_obj = db.query(Engine).filter_by(id=drv_stats.engine_id).first() if (drv_stats and drv_stats.engine_id) else None
        # Constructor champion
        team_obj = db.query(Team).filter_by(id=tm_stats.team_id).first() if tm_stats else None

        drv_engine_power = None
        if drv_stats and drv_stats.engine_id:
            eng_s = db.query(EngineSeasonStats).filter_by(engine_id=drv_stats.engine_id, season_id=s.id).first()
            if eng_s:
                drv_engine_power = int(eng_s.power * 100)

        drv_sponsor_obj = None
        if drv_stats and drv_stats.team_id:
            drv_tss = db.query(TeamSeasonStats).filter_by(team_id=drv_stats.team_id, season_id=s.id).first()
            if drv_tss and drv_tss.sponsor_id:
                drv_sponsor_obj = db.query(Sponsor).filter_by(id=drv_tss.sponsor_id).first()

        summaries.append({
            "season": s,
            "driver": driver_obj,
            "driver_flag": NATIONALITY_FLAGS.get(driver_obj.nationality, "") if driver_obj else "",
            "driver_skill": int(drv_stats.skill * 100) if drv_stats else None,
            "driver_team": drv_team_obj,
            "driver_engine": drv_engine_obj,
            "driver_engine_power": drv_engine_power,
            "driver_sponsor": drv_sponsor_obj,
            "driver_chassis": int(drv_stats.skill * 100) if drv_stats else None,
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

    for ds in driver_stats:
        ds.driver_obj = db.query(Driver).filter_by(id=ds.driver_id).first()
        ds.team_obj = db.query(Team).filter_by(id=ds.team_id).first() if ds.team_id else None
        ds.engine_obj = db.query(Engine).filter_by(id=ds.engine_id).first() if ds.engine_id else None
        ds.driver_flag = NATIONALITY_FLAGS.get(ds.driver_obj.nationality, "") if ds.driver_obj else ""

    for ts in team_stats:
        ts.team_obj = db.query(Team).filter_by(id=ts.team_id).first()
        ts.engine_obj = db.query(Engine).filter_by(id=ts.engine_id).first() if ts.engine_id else None
        if ts.engine_id:
            eng_s = db.query(EngineSeasonStats).filter_by(engine_id=ts.engine_id, season_id=season.id).first()
            ts.engine_power = int(eng_s.power * 100) if eng_s else None
        else:
            ts.engine_power = None

    for race in races:
        winner_result = db.query(RaceResult).filter_by(race_id=race.id, position=1).first()
        race.winner = db.query(Driver).filter_by(id=winner_result.driver_id).first() if winner_result else None
        race.winner_team = db.query(Team).filter_by(id=winner_result.team_id).first() if winner_result else None

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
    for r in results:
        r.driver_obj = db.query(Driver).filter_by(id=r.driver_id).first()
        r.team_obj = db.query(Team).filter_by(id=r.team_id).first()
        r.engine_obj = db.query(Engine).filter_by(id=r.engine_id).first()

    prev_round = round_num - 1 if round_num > 1 else None
    next_round = (
        round_num + 1
        if db.query(Race).filter_by(season_id=season.id, round_number=round_num + 1).first()
        else None
    )

    return templates.TemplateResponse(request, "race_detail.html", {
        "season": season,
        "race": race,
        "track": track,
        "results": results,
        "prev_round": prev_round,
        "next_round": next_round,
    })
