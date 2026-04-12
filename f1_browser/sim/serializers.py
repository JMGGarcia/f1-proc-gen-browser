"""Module-level serialisation functions extracted from WorldRunner."""
from __future__ import annotations

from sim.constants import EventType, PointsSystem
from sim.flags import NATIONALITY_FLAGS
from sim.race import LapRace


def serialise_results(results) -> list:
    points_table = PointsSystem.RACE_POINTS
    out = []
    for idx, (driver, perf) in enumerate(results):
        dnf = perf == -1.0
        pts = 0 if dnf or idx >= len(points_table) else points_table[idx]
        team = driver.team
        engine = team.engine if team else None
        sponsor = team.sponsor if team else None
        out.append({
            "pos": 0 if dnf else idx + 1,
            "driver_name": f"{driver.first_name} {driver.last_name}",
            "team_name": team.name if team else "",
            "team_color_primary": team.color_primary if team else "#333",
            "team_color_secondary": team.color_secondary if team else "#fff",
            "engine_name": engine.name if engine else "",
            "engine_color_primary": engine.color_primary if engine else "#333",
            "engine_color_secondary": engine.color_secondary if engine else "#fff",
            "sponsor_name": sponsor.name if sponsor else "",
            "sponsor_color_primary": sponsor.color_primary if sponsor else "#333",
            "sponsor_color_secondary": sponsor.color_secondary if sponsor else "#fff",
            "points": pts,
            "dnf": dnf,
        })
    return out


def serialise_lap_event(
    lap: int,
    standings: list,
    season_num: int,
    round_num: int,
    total_rounds: int,
    track,
) -> dict:
    """Serialise a lap's standings into a race_start or race_lap SSE payload.

    Returns the full payload dict. Caller should store payload["standings"] in
    _last_lap_standings and lap in _last_lap_num.
    """
    event_type = EventType.RACE_START if lap == 0 else EventType.RACE_LAP
    leader_time: float | None = None
    serialised = []
    for i, s in enumerate(standings):
        if not s.dnf and leader_time is None:
            leader_time = s.total_time
        if s.dnf:
            gap = "DNF"
        elif i == 0:
            gap = "LEADER"
        else:
            gap = f"+{s.total_time - leader_time:.3f}"
        engine = s.team.engine if s.team else None
        sponsor = s.team.sponsor if s.team else None
        serialised.append({
            "pos": i + 1,
            "driver_id": s.driver.db_id,
            "driver_name": f"{s.driver.first_name} {s.driver.last_name}",
            "driver_nat": s.driver.nationality,
            "driver_flag": NATIONALITY_FLAGS.get(s.driver.nationality, ""),
            "team_name": s.team.name,
            "team_color_primary": s.team.color_primary,
            "team_color_secondary": s.team.color_secondary,
            "engine_name": engine.name if engine else "",
            "engine_color_primary": engine.color_primary if engine else "#333",
            "engine_color_secondary": engine.color_secondary if engine else "#fff",
            "sponsor_name": sponsor.name if sponsor else "",
            "sponsor_color_primary": sponsor.color_primary if sponsor else "#333",
            "sponsor_color_secondary": sponsor.color_secondary if sponsor else "#fff",
            "gap": gap,
            "event": s.last_event,
            "dnf": s.dnf,
        })
    return {
        "type": event_type,
        "season": season_num,
        "round": round_num,
        "total_rounds": total_rounds,
        "track": {"id": track.db_id, "name": track.name},
        "lap": lap,
        "total_laps": LapRace.LAPS,
        "standings": serialised,
    }


def serialise_driver_snap(snap) -> list:
    return [
        {
            "pos": pos,
            "driver_name": f"{driver.first_name} {driver.last_name}",
            "team_name": driver.team.name if driver.team else "",
            "team_color_primary": driver.team.color_primary if driver.team else "#333",
            "team_color_secondary": driver.team.color_secondary if driver.team else "#fff",
            "points": pts,
        }
        for pos, (driver, pts) in enumerate(snap[:20], 1)
    ]


def serialise_team_snap(snap) -> list:
    return [
        {
            "pos": pos,
            "team_name": team.name,
            "color_primary": team.color_primary,
            "color_secondary": team.color_secondary,
            "points": pts,
        }
        for pos, (team, pts) in enumerate(snap[:20], 1)
    ]
