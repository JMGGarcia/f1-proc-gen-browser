from __future__ import annotations

import os
import random
from typing import List, Optional, Tuple

from db import models as m
from db.backup import cleanup_backups, get_world_id8, make_backup
from sim.constants import DriverConstants, PointsSystem, SimulationConstants, TeamConstants
from sim.drivers import Driver, DriverGenerator
from sim.countries import get_all_countries
from sim.flags import NATIONALITY_FLAGS
from sim.race import LapRace
from sim.season import Season
from sim.sponsors import Sponsor
from sim.teams import Chief, ChiefGenerator, ChiefRole, Engine, OwnerType, Team


def _is_struggling(team: Team) -> bool:
    """Return True if a team has finished outside the top STRUGGLING_THRESHOLD in 4 of the last 5 seasons."""
    ph = team.position_history
    if len(ph) < SimulationConstants.HISTORY_YEARS:
        return False
    bad = sum(1 for p in ph[-SimulationConstants.HISTORY_YEARS:] if p > TeamConstants.STRUGGLING_THRESHOLD)
    return bad >= 4


def _pick_buyer_type(teams: List[Team], engines: List[Engine], sponsors: List[Sponsor]) -> Optional[str]:
    """Pick a buyer type based on weights, respecting ownership caps."""
    engine_owner_count = sum(1 for t in teams if t.owner_type == OwnerType.ENGINE_SUPPLIER)
    sponsor_owner_count = sum(1 for t in teams if t.owner_type == OwnerType.SPONSOR)

    weights = dict(TeamConstants.BUYER_WEIGHTS)
    if engine_owner_count >= TeamConstants.MAX_ENGINE_SUPPLIER_OWNERS:
        weights[OwnerType.ENGINE_SUPPLIER] = 0
    if sponsor_owner_count >= TeamConstants.MAX_SPONSOR_OWNERS:
        weights[OwnerType.SPONSOR] = 0

    # Ensure there's an engine that doesn't already own a team
    owning_engine_ids = {t.owner_engine.db_id for t in teams if t.owner_engine}
    free_owner_engines = [e for e in engines if e.db_id not in owning_engine_ids]
    if not free_owner_engines:
        weights[OwnerType.ENGINE_SUPPLIER] = 0

    # Ensure there's a large sponsor available
    free_large_sponsors = [s for s in sponsors if s.tier == "large" and s.team is None]
    if not free_large_sponsors:
        weights[OwnerType.SPONSOR] = 0

    total = sum(weights.values())
    if total == 0:
        return None

    btypes = list(weights.keys())
    bweights = list(weights.values())
    return random.choices(btypes, weights=bweights, k=1)[0]


class WorldRunner:
    def __init__(
        self,
        tracks,
        engines: List[Engine],
        teams: List[Team],
        drivers: List[Driver],
        driver_generator: DriverGenerator,
        n_seasons: int,
        sponsors: Optional[List[Sponsor]] = None,
        chief_generator: Optional[ChiefGenerator] = None,
        chiefs: Optional[List[Chief]] = None,
    ):
        self.tracks = tracks
        self.engines = engines
        self.teams = teams
        self.drivers = drivers
        self.driver_generator = driver_generator
        self.n_seasons = n_seasons
        self.sponsors: List[Sponsor] = sponsors or []
        self.chief_generator: ChiefGenerator = chief_generator or ChiefGenerator(
            names_dir=driver_generator.names_dir
        )
        self.chiefs: List[Chief] = chiefs or []

        # Tick-loop state (lap-by-lap live feed)
        self._tick_season: Optional[Season] = None
        self._tick_season_num: int = 0
        self._tick_season_id: int = 0
        self._tick_race_records: list = []
        self._tick_total_rounds: int = len(tracks)

        # Pending races this season: [(round_num, track), ...]
        self._pending_rounds: list = []

        # Active lap iterator for the current race
        self._current_lap_race = None
        self._current_lap_iter = None
        self._current_round_num: int = 0
        self._current_db_race_id: int = 0
        self._current_track = None

        # Last broadcasted lap state (for snapshot on reconnect)
        self._last_lap_num: int = 0
        self._last_lap_standings: list = []

    # ------------------------------------------------------------------ #
    # Tick-based live feed                                                 #
    # ------------------------------------------------------------------ #

    def tick_one_lap(self, db) -> list[dict]:
        """Advance simulation by one lap. Returns SSE payload list.
        One payload per call; two payloads at season end (standings + events)."""
        if self._tick_season is None:
            self._tick_start_season(db)

        # Start a new race when there is no active lap iterator
        if self._current_lap_iter is None:
            if not self._pending_rounds:
                return self._tick_finish_season(db)

            self._current_round_num, self._current_track = self._pending_rounds.pop(0)
            self._current_lap_race = LapRace(self._current_track)
            self._current_lap_iter = self._current_lap_race.iter_laps(self.teams)

            # Create the Race row now so the page URL is immediately reachable
            db_race = m.Race(
                season_id=self._tick_season_id,
                track_id=self._current_track.db_id,
                round_number=self._current_round_num,
            )
            db.add(db_race)
            db.flush()
            db.commit()
            self._current_db_race_id = db_race.id

            print(
                f"  [tick] Season {self._tick_season_num} "
                f"round {self._current_round_num}/{self._tick_total_rounds}: "
                f"{self._current_track.name}",
                flush=True,
            )

        # Advance one lap
        try:
            lap_num, standings = next(self._current_lap_iter)
            return [self._serialise_lap_event(lap_num, standings)]
        except StopIteration:
            # All 50 laps done — finalise the race
            results = self._current_lap_race.get_results()

            # Award championship points
            points_table = PointsSystem.RACE_POINTS
            for idx, (driver, time_val) in enumerate(results):
                if time_val != -1.0 and idx < len(points_table):
                    self._tick_season._award_points(driver, points_table[idx])

            self._write_race_results_for(db, self._current_db_race_id, results)
            self._tick_race_records.append((self._current_track, results))
            db.commit()

            driver_snap = sorted(
                self._tick_season.classification_driver.items(),
                key=lambda x: x[1], reverse=True,
            )
            team_snap = sorted(
                self._tick_season.classification_team.items(),
                key=lambda x: x[1], reverse=True,
            )

            self._current_lap_iter = None
            self._current_lap_race = None

            return [{
                "type": "race_result",
                "season": self._tick_season_num,
                "round": self._current_round_num,
                "total_rounds": self._tick_total_rounds,
                "track": {"id": self._current_track.db_id, "name": self._current_track.name},
                "results": self._serialise_results(results),
                "driver_standings": self._serialise_driver_snap(driver_snap),
                "team_standings": self._serialise_team_snap(team_snap),
            }]

    def get_live_race_state(self) -> dict | None:
        """Return current live lap state for snapshot on page load. None if no race active."""
        if self._current_track is None or not self._last_lap_standings:
            return None
        return {
            "active": self._current_lap_iter is not None,
            "season": self._tick_season_num,
            "round": self._current_round_num,
            "total_rounds": self._tick_total_rounds,
            "track_name": self._current_track.name,
            "lap": self._last_lap_num,
            "total_laps": LapRace.LAPS,
            "standings": self._last_lap_standings,
        }

    def _tick_start_season(self, db) -> None:
        in_progress = (
            db.query(m.Season)
            .filter_by(completed=False)
            .order_by(m.Season.number.desc())
            .first()
        )
        if in_progress:
            self._tick_resume_season(db, in_progress)
            return

        latest = db.query(m.Season).order_by(m.Season.number.desc()).first()
        self._tick_season_num = (latest.number + 1) if latest else 1
        db_season = m.Season(number=self._tick_season_num, completed=False)
        db.add(db_season)
        db.flush()
        self._tick_season_id = db_season.id
        self._tick_season = Season(self.tracks, self.teams, self._tick_season_num)
        self._tick_race_records = []
        self._pending_rounds = list(enumerate(self.tracks, 1))
        self._current_lap_iter = None
        self._current_lap_race = None
        print(f"  [tick] Starting season {self._tick_season_num}…", flush=True)

    def _tick_resume_season(self, db, in_progress: "m.Season") -> None:
        self._tick_season_num = in_progress.number
        self._tick_season_id = in_progress.id

        driver_map = {d.db_id: d for d in self.drivers}
        team_map = {t.db_id: t for t in self.teams}
        track_map = {t.db_id: t for t in self.tracks}

        done_races = (
            db.query(m.Race)
            .filter_by(season_id=in_progress.id)
            .order_by(m.Race.round_number)
            .all()
        )
        done_round_nums = {r.round_number for r in done_races}

        # Rebuild driver-team assignments from this season's race results.
        # The loader reconstructs Season N lineups from DriverSeasonStats, but
        # Season N+1 may have different signings. We derive the correct lineup
        # directly from the DB results so team.drivers and driver.team are right
        # both for the classification display and for the remaining races.
        if done_races:
            season_driver_team: dict[int, int] = {}  # driver_id → team_id
            for db_race in done_races:
                for result in db.query(m.RaceResult).filter_by(race_id=db_race.id).all():
                    if result.driver_id not in season_driver_team:
                        season_driver_team[result.driver_id] = result.team_id

            # Reset all slots, then re-assign from race data
            for team in self.teams:
                team.drivers = [None, None]
            for driver in self.drivers:
                driver.team = None

            for driver_id, team_id in season_driver_team.items():
                driver = driver_map.get(driver_id)
                team = team_map.get(team_id)
                if driver is None or team is None:
                    continue
                driver.team = team
                if team.drivers[0] is None:
                    team.drivers[0] = driver
                elif team.drivers[1] is None:
                    team.drivers[1] = driver

        # Create Season with the now-correct driver assignments
        self._tick_season = Season(self.tracks, self.teams, self._tick_season_num)
        self._tick_race_records = []

        points_table = PointsSystem.RACE_POINTS
        for db_race in done_races:
            race_results = (
                db.query(m.RaceResult)
                .filter_by(race_id=db_race.id)
                .order_by(m.RaceResult.position)
                .all()
            )
            winner_driver = None
            for result in race_results:
                if result.dnf or result.position == 0:
                    continue
                idx = result.position - 1
                if idx >= len(points_table):
                    continue
                driver = driver_map.get(result.driver_id)
                if driver is None:
                    continue
                pts = points_table[idx]
                self._tick_season.classification_driver[driver] = (
                    self._tick_season.classification_driver.get(driver, 0) + pts
                )
                if driver.team is not None:
                    self._tick_season.classification_team[driver.team] = (
                        self._tick_season.classification_team.get(driver.team, 0) + pts
                    )
                if idx == 0:
                    winner_driver = driver

            # Minimal stub entry: only winner is needed by _write_season_stats
            fake_results = [(winner_driver, 1.0)] if winner_driver else []
            track = track_map.get(db_race.track_id)
            if track:
                self._tick_race_records.append((track, fake_results))

        n_done = len(done_round_nums)
        n_total = len(self.tracks)
        print(
            f"  [tick] Resuming season {self._tick_season_num} "
            f"({n_done}/{n_total} races done)…",
            flush=True,
        )
        self._pending_rounds = [
            (round_num, track)
            for round_num, track in enumerate(self.tracks, 1)
            if round_num not in done_round_nums
        ]
        self._current_lap_iter = None
        self._current_lap_race = None

    def _tick_finish_season(self, db) -> dict:
        season_num = self._tick_season_num
        season_id = self._tick_season_id
        race_records = self._tick_race_records
        season = self._tick_season

        sorted_drivers = sorted(season.classification_driver.items(), key=lambda x: x[1], reverse=True)
        sorted_teams = sorted(season.classification_team.items(), key=lambda x: x[1], reverse=True)

        self._write_season_stats(db, season_id, race_records, sorted_drivers, sorted_teams)
        db.query(m.Season).filter_by(id=season_id).update({"completed": True})
        db.flush()

        # Serialise standings NOW, before off-season mutations change driver.team / team names
        serialised_driver_standings = self._serialise_driver_snap(sorted_drivers)
        serialised_team_standings = self._serialise_team_snap(sorted_teams)
        driver_champion_name = f"{sorted_drivers[0][0].first_name} {sorted_drivers[0][0].last_name}"
        driver_champion_pts = sorted_drivers[0][1]
        team_champion_name = sorted_teams[0][0].name
        team_champion_pts = sorted_teams[0][1]

        winning_driver = sorted_drivers[0][0]
        winning_team = sorted_teams[0][0]

        self._update_position_history(sorted_teams)
        self._update_chiefs(db, season_num, sorted_teams)
        self._tick_chief_contracts(db, season_num)
        self._match_chiefs_to_teams(db, season_num, sorted_teams)
        sorted_teams = self._check_team_sales(db, season_num, sorted_teams)
        self._age_drivers(db, season_num)
        self._tweak_chassis_engine(db, season_num, season_id)
        self._teams_pick_engines(db, season_num, sorted_teams, winning_driver, winning_team)
        self._teams_pick_sponsors(db, season_num, sorted_teams)
        self._teams_pick_drivers(db, season_num, sorted_teams, winning_driver, winning_team)
        self._tweak_driver_form()
        db.commit()
        make_backup(season_num, db)
        cleanup_backups(get_world_id8(db))

        world_events = (
            db.query(m.WorldEvent)
            .filter_by(season_number=season_num)
            .order_by(m.WorldEvent.id)
            .all()
        )

        # Reset tick state
        self._tick_race_records = []
        self._tick_season = None
        self._pending_rounds = []
        self._current_lap_iter = None
        self._current_lap_race = None
        self._current_track = None
        self._current_db_race_id = 0
        self._last_lap_standings = []

        print(f"  [tick] Season {season_num} complete.", flush=True)
        return [
            {
                "type": "season_standings",
                "season": season_num,
                "driver_champion": {
                    "driver_name": driver_champion_name,
                    "points": driver_champion_pts,
                },
                "team_champion": {
                    "team_name": team_champion_name,
                    "points": team_champion_pts,
                },
                "driver_standings": serialised_driver_standings,
                "team_standings": serialised_team_standings,
            },
            {
                "type": "season_events",
                "season": season_num,
                "world_events": [
                    {"event_type": e.event_type, "description": e.description}
                    for e in world_events
                ],
            },
        ]

    def _write_race_results_for(self, db, db_race_id: int, results) -> None:
        """Write RaceResult rows into an already-created Race row (tick-mode)."""
        points_table = PointsSystem.RACE_POINTS
        for idx, (driver, time_val) in enumerate(results):
            dnf = time_val == -1.0
            position = 0 if dnf else idx + 1
            pts = 0 if dnf or idx >= len(points_table) else points_table[idx]
            db.add(m.RaceResult(
                race_id=db_race_id,
                driver_id=driver.db_id,
                team_id=driver.team.db_id,
                engine_id=driver.team.engine.db_id,
                position=position,
                points=pts,
                dnf=dnf,
                total_time=None if dnf else time_val,
            ))

    def _write_one_race(self, db, season_id: int, round_num: int, track, results) -> None:
        points_table = PointsSystem.RACE_POINTS
        db_race = m.Race(season_id=season_id, track_id=track.db_id, round_number=round_num)
        db.add(db_race)
        db.flush()
        for idx, (driver, time_val) in enumerate(results):
            dnf = time_val == -1.0
            position = 0 if dnf else idx + 1
            pts = 0 if dnf or idx >= len(points_table) else points_table[idx]
            db.add(m.RaceResult(
                race_id=db_race.id,
                driver_id=driver.db_id,
                team_id=driver.team.db_id,
                engine_id=driver.team.engine.db_id,
                position=position,
                points=pts,
                dnf=dnf,
                total_time=None if dnf else time_val,
            ))

    @staticmethod
    def _serialise_results(results) -> list:
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

    def _serialise_lap_event(self, lap: int, standings: list) -> dict:
        """Serialise a lap's standings into a race_start or race_lap SSE payload."""
        event_type = "race_start" if lap == 0 else "race_lap"
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
        self._last_lap_num = lap
        self._last_lap_standings = serialised
        return {
            "type": event_type,
            "season": self._tick_season_num,
            "round": self._current_round_num,
            "total_rounds": self._tick_total_rounds,
            "track": {"id": self._current_track.db_id, "name": self._current_track.name},
            "lap": lap,
            "total_laps": LapRace.LAPS,
            "standings": serialised,
        }

    @staticmethod
    def _serialise_driver_snap(snap) -> list:
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

    @staticmethod
    def _serialise_team_snap(snap) -> list:
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

    def run(self, db):
        for n in range(self.n_seasons):
            self.run_one_season(db, n + 1)

    def run_one_season(self, db, season_num: int):
        print(f"  Simulating season {season_num}...", flush=True)

        db_season = m.Season(number=season_num, completed=False)
        db.add(db_season)
        db.flush()
        season_id = db_season.id

        season = Season(self.tracks, self.teams, season_num)
        race_records, sorted_drivers, sorted_teams = season.run()

        self._write_race_results(db, season_id, race_records)
        self._write_season_stats(db, season_id, race_records, sorted_drivers, sorted_teams)
        db.query(m.Season).filter_by(id=season_id).update({"completed": True})
        db.flush()

        winning_driver = sorted_drivers[0][0]
        winning_team = sorted_teams[0][0]

        self._update_position_history(sorted_teams)
        self._update_chiefs(db, season_num, sorted_teams)
        self._tick_chief_contracts(db, season_num)
        self._match_chiefs_to_teams(db, season_num, sorted_teams)
        sorted_teams = self._check_team_sales(db, season_num, sorted_teams)
        self._age_drivers(db, season_num)
        self._tweak_chassis_engine(db, season_num, season_id)
        self._teams_pick_engines(db, season_num, sorted_teams, winning_driver, winning_team)
        self._teams_pick_sponsors(db, season_num, sorted_teams)
        self._teams_pick_drivers(db, season_num, sorted_teams, winning_driver, winning_team)
        self._tweak_driver_form()
        db.commit()
        make_backup(season_num, db)
        cleanup_backups(get_world_id8(db))

    # ------------------------------------------------------------------ #
    # DB writers                                                           #
    # ------------------------------------------------------------------ #

    def _write_race_results(self, db, season_id: int, race_records):
        points_table = PointsSystem.RACE_POINTS
        for round_num, (track, results) in enumerate(race_records, 1):
            db_race = m.Race(season_id=season_id, track_id=track.db_id, round_number=round_num)
            db.add(db_race)
            db.flush()

            for idx, (driver, time_val) in enumerate(results):
                dnf = time_val == -1.0
                position = 0 if dnf else idx + 1
                pts = 0 if dnf or idx >= len(points_table) else points_table[idx]
                db.add(m.RaceResult(
                    race_id=db_race.id,
                    driver_id=driver.db_id,
                    team_id=driver.team.db_id,
                    engine_id=driver.team.engine.db_id,
                    position=position,
                    points=pts,
                    dnf=dnf,
                    total_time=None if dnf else time_val,
                ))

    def _write_season_stats(self, db, season_id: int, race_records, sorted_drivers, sorted_teams):
        # Build points maps
        driver_points = {d: pts for d, pts in sorted_drivers}
        team_points = {t: pts for t, pts in sorted_teams}

        # Pre-count wins per driver from race_records
        win_counts: dict = {}
        for _track, results in race_records:
            if results:
                winner = results[0][0]
                win_counts[winner.db_id] = win_counts.get(winner.db_id, 0) + 1

        for pos, (driver, _) in enumerate(sorted_drivers, 1):
            db.add(m.DriverSeasonStats(
                driver_id=driver.db_id,
                season_id=season_id,
                team_id=driver.team.db_id if driver.team else None,
                engine_id=driver.team.engine.db_id if (driver.team and driver.team.engine) else None,
                age=driver.age,
                skill=driver.skill,
                top_skill=driver.top_skill,
                total_points=driver_points.get(driver, 0),
                championship_position=pos,
                wins=win_counts.get(driver.db_id, 0),
            ))

        for pos, (team, _) in enumerate(sorted_teams, 1):
            db.add(m.TeamSeasonStats(
                team_id=team.db_id,
                season_id=season_id,
                engine_id=team.engine.db_id if team.engine else None,
                chassis=team.chassis,
                owner_chief_id=team.owner.db_id if team.owner else None,
                cto_chief_id=team.cto.db_id if team.cto else None,
                cmo_chief_id=team.cmo.db_id if team.cmo else None,
                cpo_chief_id=team.cpo.db_id if team.cpo else None,
                owner_skill=team.owner.skill_primary if team.owner else None,
                cto_development=team.cto.skill_primary if team.cto else None,
                cto_eng_scouting=team.cto.skill_secondary if team.cto else None,
                cpo_scouting=team.cpo.skill_primary if team.cpo else None,
                total_points=team_points.get(team, 0),
                championship_position=pos,
                sponsor_id=team.sponsor.db_id if team.sponsor else None,
                owner_type=team.owner_type,
            ))

        for engine in self.engines:
            db.add(m.EngineSeasonStats(
                engine_id=engine.db_id,
                season_id=season_id,
                power=engine.power,
                reliability=engine.reliability,
            ))

    def _emit_event(self, db, season_num: int, event_type: str, description: str):
        db.add(m.WorldEvent(
            season_number=season_num,
            event_type=event_type,
            description=description,
        ))

    # ------------------------------------------------------------------ #
    # Post-season sim updates                                              #
    # ------------------------------------------------------------------ #

    def _update_position_history(self, sorted_teams):
        """Update each team's position_history from the finished season results."""
        for idx, (team, _) in enumerate(sorted_teams, 1):
            team.position_history.append(idx)
            if len(team.position_history) > SimulationConstants.HISTORY_YEARS:
                team.position_history.pop(0)

    def _retire_chief(self, db, chief: Chief, season_num: int) -> None:
        """Retire a non-owner chief, hard-delete if never employed, then spawn a replacement into the pool."""
        was_employed = db.query(m.TeamSeasonStats).filter(
            (m.TeamSeasonStats.owner_chief_id == chief.db_id)
            | (m.TeamSeasonStats.cto_chief_id == chief.db_id)
            | (m.TeamSeasonStats.cmo_chief_id == chief.db_id)
            | (m.TeamSeasonStats.cpo_chief_id == chief.db_id)
        ).first() is not None

        if was_employed:
            db.query(m.TeamChief).filter_by(id=chief.db_id).update({
                "retired": True, "retired_season": season_num,
            })
        else:
            db.query(m.TeamChief).filter_by(id=chief.db_id).delete()

        chief.retired = True
        if chief in self.chiefs:
            self.chiefs.remove(chief)

        # Spawn a replacement into the free-agent pool
        role_gen_map = {
            ChiefRole.CTO: self.chief_generator.generate_cto,
            ChiefRole.CMO: self.chief_generator.generate_cmo,
            ChiefRole.CPO: self.chief_generator.generate_cpo,
        }
        gen_method = role_gen_map.get(chief.role)
        if gen_method is None:
            return  # owners have succession; no pool replacement

        new_chief = gen_method(random.choice([c.code for c in get_all_countries()]))
        db_c = m.TeamChief(
            first_name=new_chief.first_name,
            last_name=new_chief.last_name,
            nationality=new_chief.nationality,
            role=new_chief.role,
            age=new_chief.age,
            skill_primary=new_chief.skill_primary,
            skill_secondary=new_chief.skill_secondary,
            team_id=None,
            contract_years=new_chief.contract_years,
            retired=False,
        )
        db.add(db_c)
        db.flush()
        new_chief.db_id = db_c.id
        new_chief.team = None
        self.chiefs.append(new_chief)
        self._emit_event(
            db, season_num, "chief_debut",
            f"{new_chief.first_name} {new_chief.last_name} "
            f"(age {new_chief.age}) entered the {new_chief.role.upper()} pool.",
        )

    def _update_chiefs(self, db, season_num: int, sorted_teams):
        """Age all chiefs, tick skill updates, handle owner succession."""
        for chief in list(self.chiefs):
            chief.age += 1
            chief.yearly_skill_update()
            db.query(m.TeamChief).filter_by(id=chief.db_id).update({
                "age": chief.age,
                "skill_primary": chief.skill_primary,
                "skill_secondary": chief.skill_secondary,
            })
            # Free-agent non-owner retirement (covers chiefs released from teams who now age out)
            if chief.role != ChiefRole.OWNER and chief.team is None:
                if chief.should_retire_as_free_agent():
                    self._retire_chief(db, chief, season_num)
                continue

            # Owner retirement
            if chief.role == ChiefRole.OWNER and chief.age >= TeamConstants.OWNER_RETIRE_AGE:
                team = chief.team
                if team is None:
                    continue
                db.query(m.TeamChief).filter_by(id=chief.db_id).update({
                    "retired": True, "retired_season": season_num,
                })
                chief.retired = True
                self.chiefs.remove(chief)
                team.owner = None
                # Generate successor
                successor = self.chief_generator.generate_owner_successor(chief)
                successor.team = team
                db_s = m.TeamChief(
                    first_name=successor.first_name,
                    last_name=successor.last_name,
                    nationality=successor.nationality,
                    role=ChiefRole.OWNER,
                    age=successor.age,
                    skill_primary=successor.skill_primary,
                    skill_secondary=successor.skill_secondary,
                    team_id=team.db_id,
                    contract_years=successor.contract_years,
                    retired=False,
                )
                db.add(db_s)
                db.flush()
                successor.db_id = db_s.id
                team.owner = successor
                self.chiefs.append(successor)
                self._emit_event(
                    db, season_num, "chief_succession",
                    f"{team.name}: {successor.first_name} {successor.last_name} succeeded "
                    f"{chief.first_name} {chief.last_name} as team owner (age {chief.age}).",
                )

    def _tick_chief_contracts(self, db, season_num: int):
        """Decrement non-owner contracts; release or retire expired chiefs."""
        for team in self.teams:
            for role_attr in ("cto", "cmo", "cpo"):
                chief: Optional[Chief] = getattr(team, role_attr)
                if chief is None:
                    continue
                chief.tick_contract()
                if not chief.is_contract_expired():
                    continue
                if chief.should_retire_as_free_agent():
                    setattr(team, role_attr, None)
                    chief.team = None
                    db.query(m.TeamChief).filter_by(id=chief.db_id).update({"team_id": None})
                    self._retire_chief(db, chief, season_num)
                else:
                    # Release to free agent pool
                    db.query(m.TeamChief).filter_by(id=chief.db_id).update({"team_id": None})
                    chief.team = None
                    setattr(team, role_attr, None)
                    self._emit_event(
                        db, season_num, "chief_free_agent",
                        f"{chief.name} ({chief.role.upper()}) is now a free agent.",
                    )

    def _match_chiefs_to_teams(self, db, season_num: int, sorted_teams):
        """Hire free-agent chiefs for vacant roles."""
        for role_attr, role_str, gen_method in (
            ("cto", ChiefRole.CTO, self.chief_generator.generate_cto),
            ("cmo", ChiefRole.CMO, self.chief_generator.generate_cmo),
            ("cpo", ChiefRole.CPO, self.chief_generator.generate_cpo),
        ):
            free_chiefs = [c for c in self.chiefs if c.role == role_str and c.team is None and not c.retired]
            for team, _ in sorted_teams:
                if getattr(team, role_attr) is not None:
                    continue
                if not free_chiefs:
                    # Safety net: pool is unexpectedly empty — generate one directly
                    print(
                        f"  [WARN] Season {season_num}: {role_str.upper()} pool empty, generating on demand.",
                        flush=True,
                    )
                    chosen = gen_method(random.choice([c.code for c in get_all_countries()]))
                    db_c = m.TeamChief(
                        first_name=chosen.first_name,
                        last_name=chosen.last_name,
                        nationality=chosen.nationality,
                        role=role_str,
                        age=chosen.age,
                        skill_primary=chosen.skill_primary,
                        skill_secondary=chosen.skill_secondary,
                        team_id=None,
                        contract_years=chosen.contract_years,
                        retired=False,
                    )
                    db.add(db_c)
                    db.flush()
                    chosen.db_id = db_c.id
                    chosen.team = None
                    self.chiefs.append(chosen)
                    free_chiefs.append(chosen)
                rated = self._compute_chief_perception(team, free_chiefs)
                chosen = rated[0]
                free_chiefs.remove(chosen)
                contract = random.randint(
                    TeamConstants.CHIEF_CONTRACT_MIN, TeamConstants.CHIEF_CONTRACT_MAX
                )
                chosen.contract_years = contract
                chosen.team = team
                setattr(team, role_attr, chosen)
                db.query(m.TeamChief).filter_by(id=chosen.db_id).update({
                    "team_id": team.db_id,
                    "contract_years": contract,
                })
                self._emit_event(
                    db, season_num, "chief_signing",
                    f"{team.name} signed {chosen.name} as {role_str.upper()} "
                    f"(skill {chosen.skill_primary}) on a {contract}-year contract.",
                )

    def _compute_chief_perception(self, team: Team, candidates: List[Chief]) -> List[Chief]:
        factor = SimulationConstants.SCOUTING_TRUE_FACTOR
        owner_f = factor + team.owner_scouting_factor * (1 - factor)
        scored = [
            (c.skill_primary * owner_f + random.random() * (1 - owner_f), i, c)
            for i, c in enumerate(candidates)
        ]
        return [c for _, _i, c in sorted(scored, reverse=True)]

    def _age_drivers(self, db, season_num: int):
        to_retire = []
        for driver in self.drivers:
            driver.age_driver()
            db.query(m.Driver).filter_by(id=driver.db_id).update({
                "age": driver.age,
                "skill": driver.skill,
                "top_skill": driver.top_skill,
            })
            # Retire free-pool drivers who are too old (on-team drivers are
            # handled by _take_driver_from_team when their contract expires)
            if driver.team is None:
                retire = driver.age > DriverConstants.RETIREMENT_AGE or (
                    driver.age > DriverConstants.EARLY_RETIREMENT_AGE
                    and random.random() < DriverConstants.EARLY_RETIREMENT_CHANCE
                )
                if retire:
                    to_retire.append(driver)

        for driver in to_retire:
            self._emit_event(
                db, season_num, "driver_retirement",
                f"{driver.name} {driver.flag} retired from racing at age {driver.age} "
                f"with a peak skill of {driver.top_skill_100}.",
            )
            self._retire_driver(db, driver, season_num)

    def _tweak_chassis_engine(self, db, season_num: int, season_id: int):
        revolution = random.random() < SimulationConstants.REVOLUTION_PROBABILITY
        random_factor = 0.3

        if revolution:
            self._emit_event(
                db, season_num, "formula_revolution",
                "A technical regulation change shakes up the order! Car performance values have been reset.",
            )

        for team in self.teams:
            if revolution:
                team.chassis = (
                    (1 - SimulationConstants.REVOLUTION_EFFECT) * team.chassis
                    + SimulationConstants.REVOLUTION_EFFECT * random.random()
                )
            delta = random.random() * random_factor - random_factor / 2
            delta += team.cto_development * SimulationConstants.TEAM_DEVELOPMENT_INFLUENCE
            team.chassis = min(1.0, max(0.0, team.chassis + delta))

        for engine in self.engines:
            owns_a_team = any(
                t.owner_type == OwnerType.ENGINE_SUPPLIER and t.owner_engine is engine
                for t in engine.teams
            )
            if revolution:
                engine.power = (
                    (1 - SimulationConstants.REVOLUTION_EFFECT) * engine.power
                    + SimulationConstants.REVOLUTION_EFFECT * random.random()
                )
                if owns_a_team:
                    engine.power = max(TeamConstants.ENGINE_SUPPLIER_REVOLUTION_MIN, engine.power)
            delta = random.random() * random_factor - random_factor / 2
            engine.power = min(1.0, max(0.0, engine.power + delta))
            if revolution and engine.teams:
                engine.power = max(TeamConstants.ENGINE_IN_USE_REVOLUTION_MIN, engine.power)

            # Engine supplier ownership bonus: extra investment in development
            if owns_a_team:
                engine.power = min(1.0, engine.power + TeamConstants.ENGINE_OWNER_POWER_BONUS)

            if revolution:
                engine.reliability -= random.random() * 0.2 + 0.3
            engine.reliability += random.random() * 0.1 + 0.05
            engine.reliability = min(
                SimulationConstants.MAXIMUM_RELIABILITY,
                max(SimulationConstants.MINIMUM_RELIABILITY, engine.reliability),
            )
            engine.update_value()

            # Sync updated values back to DB engine row
            db.query(m.Engine).filter_by(id=engine.db_id).update({
                "power": engine.power,
                "reliability": engine.reliability,
                "value": engine.value,
            })

    def _tweak_driver_form(self):
        for team in self.teams:
            for driver in team.drivers:
                if driver is None:
                    continue
                r = random.random()
                if r < DriverConstants.FORM_LOW_THRESHOLD:
                    driver.set_skill("L")
                elif r > DriverConstants.FORM_HIGH_THRESHOLD:
                    driver.set_skill("H")
                else:
                    driver.set_skill("M")

    def _take_driver_from_team(self, db, team: Team, idx: int, season_num: int):
        driver = team.drivers[idx]
        if driver is None:
            return
        team.drivers[idx] = None
        team.driver_contracts[idx] = -1
        driver.team = None

        retire = driver.age > DriverConstants.RETIREMENT_AGE or (
            driver.age > DriverConstants.EARLY_RETIREMENT_AGE
            and random.random() < DriverConstants.EARLY_RETIREMENT_CHANCE
        )
        if retire:
            self._emit_event(
                db, season_num, "driver_retirement",
                f"{driver.name} {driver.flag} retired from racing at age {driver.age} "
                f"with a peak skill of {driver.top_skill_100}.",
            )
            self._retire_driver(db, driver, season_num)

    def _teams_pick_sponsors(self, db, season_num: int, sorted_teams):
        if not self.sponsors:
            return
        total_teams = len(self.teams)

        def _score_sponsor(sponsor, team, prev_sponsor):
            score = 0
            if prev_sponsor is not None and sponsor is prev_sponsor:
                score += 1
            if sponsor.country and team.country and sponsor.country == team.country:
                score += 1
            if sponsor.country:
                for driver in team.drivers:
                    if driver and driver.country and driver.country == sponsor.country:
                        score += 1
            r1, g1, b1 = team.rgb_primary
            r2, g2, b2 = sponsor.rgb_primary
            if ((r1-r2)**2 + (g1-g2)**2 + (b1-b2)**2) ** 0.5 <= 80:
                score += 1
            if team.engine and team.engine.rgb_primary:
                r3, g3, b3 = team.engine.rgb_primary
                if ((r3-r2)**2 + (g3-g2)**2 + (b3-b2)**2) ** 0.5 <= 80:
                    score += 1
            return score

        # Release expired contracts — but give a 75% chance of renewal if the
        # sponsor tier still matches the team's current standing
        position_map_pre = {team: pos for pos, (team, _) in enumerate(sorted_teams, 1)}
        total_teams = len(self.teams)
        expiring_map: dict = {}

        for team in self.teams:
            if not team.sponsor:
                continue
            team.tick_sponsor_contract()
            if team.is_sponsor_contract_still_valid():
                continue
            # Sponsor-owned teams always keep their owner sponsor — extend instead of releasing
            if team.owner_type == OwnerType.SPONSOR and team.owner_sponsor is not None:
                new_contract = TeamConstants.SPONSOR_OWNER_CONTRACT
                team.sponsor_contract = new_contract
                db.query(m.Team).filter_by(id=team.db_id).update({"sponsor_contract": new_contract})
                continue
            expiring_sponsor = team.sponsor
            pos = position_map_pre.get(team, total_teams)

            # What tier is this team eligible for?
            if pos <= max(5, total_teams // 2):
                max_eligible = "large"
            elif pos <= max(total_teams - 3, total_teams * 7 // 10):
                max_eligible = "medium"
            else:
                max_eligible = "small"

            tier_rank = {"large": 3, "medium": 2, "small": 1}
            sponsor_still_fits = tier_rank[expiring_sponsor.tier] <= tier_rank[max_eligible]

            if sponsor_still_fits and random.random() < 0.75:
                # Renew — extend contract without changing sponsor
                new_contract = self._random_sponsor_contract(expiring_sponsor.tier)
                team.sponsor_contract = new_contract
                db.query(m.Team).filter_by(id=team.db_id).update({
                    "sponsor_contract": new_contract,
                })
                self._emit_event(
                    db, season_num, "engine_deal",
                    f"{team.name} renewed their sponsorship deal with {expiring_sponsor.name} "
                    f"for {new_contract} more year(s).",
                )
            else:
                self._emit_event(
                    db, season_num, "engine_deal",
                    f"{team.name}'s sponsorship deal with {expiring_sponsor.name} has ended.",
                )
                expiring_map[team] = expiring_sponsor
                team.remove_sponsor()

        # Teams without a sponsor pick in finishing order (best team picks first)
        for team, _ in sorted_teams:
            if team.sponsor is not None:
                continue
            # Sponsor-owned team detached somehow — re-attach owner sponsor
            if team.owner_type == OwnerType.SPONSOR and team.owner_sponsor is not None:
                contract = TeamConstants.SPONSOR_OWNER_CONTRACT
                team.sponsor = team.owner_sponsor
                team.sponsor_contract = contract
                team.owner_sponsor.assign_team(team)
                db.query(m.Team).filter_by(id=team.db_id).update({
                    "sponsor_id": team.owner_sponsor.db_id,
                    "sponsor_contract": contract,
                })
                continue
            pos = position_map_pre.get(team, total_teams)

            # Determine eligible tiers based on finishing position
            eligible_tiers = ["small"]
            if pos <= max(total_teams - 3, total_teams * 7 // 10):
                eligible_tiers.append("medium")
            if pos <= max(5, total_teams // 2):
                eligible_tiers.append("large")

            # Pick the highest-scoring sponsor from the best eligible tier.
            tier_order = [t for t in ["large", "medium", "small"] if t in eligible_tiers]
            prev_sponsor = expiring_map.get(team)
            chosen = None

            for tier in tier_order:
                candidates = [s for s in self.sponsors if s.team is None and s.tier == tier]
                if candidates:
                    chosen = max(candidates, key=lambda s: _score_sponsor(s, team, prev_sponsor))
                    break

            if chosen is None:
                # Fallback: any available sponsor
                fallback = [s for s in self.sponsors if s.team is None]
                if not fallback:
                    continue
                chosen = max(fallback, key=lambda s: _score_sponsor(s, team, prev_sponsor))

            contract = self._random_sponsor_contract(chosen.tier)
            team.sponsor = chosen
            team.sponsor_contract = contract
            chosen.assign_team(team)
            db.query(m.Team).filter_by(id=team.db_id).update({
                "sponsor_id": chosen.db_id,
                "sponsor_contract": contract,
            })
            self._emit_event(
                db, season_num, "engine_deal",
                f"{team.name} signed a {contract}-year sponsorship deal with {chosen.name}.",
            )

    def _teams_pick_drivers(self, db, season_num: int, sorted_teams, winning_driver: Driver, winning_team: Team):
        # Release expired contracts
        for team in self.teams:
            team.tick_driver_contracts()
            drv1_ok, drv2_ok = team.are_driver_contracts_valid()
            if not drv1_ok:
                self._take_driver_from_team(db, team, 0, season_num)
            if not drv2_ok:
                self._take_driver_from_team(db, team, 1, season_num)

        # Fill empty seats via two-sided matching
        prev_drv_champ_team = winning_driver.team  # may be None if driver retired mid-season
        prev_team_champ = winning_team
        self._match_drivers_to_teams(db, season_num, sorted_teams, prev_drv_champ_team, prev_team_champ)

        # VALIDATION: Ensure consistency after matching
        team_driver_set = set()
        for team in self.teams:
            for i, driver in enumerate(team.drivers):
                if driver is not None:
                    team_driver_set.add(id(driver))
                    if driver.team is not team:
                        print(
                            f"  [ERROR] Season {season_num}: {driver.name} in {team.name}.drivers[{i}] "
                            f"but driver.team = {driver.team.name if driver.team else None}",
                            flush=True,
                        )

        for driver in self.drivers:
            if id(driver) not in team_driver_set:
                # Driver should not be assigned to any team
                if driver.team is not None:
                    print(
                        f"  [ERROR] Season {season_num}: {driver.name} is FREE but driver.team = {driver.team.name}",
                        flush=True,
                    )
                    driver.team = None  # Force clear it

        drivers_in_teams = sum(1 for team in self.teams for driver in team.drivers if driver is not None)
        total_teams = len(self.teams)

        # Sync all teams' driver contract data to DB
        for team in self.teams:
            self._sync_team_to_db(db, team)

    def _teams_pick_engines(self, db, season_num: int, sorted_teams, winning_driver: Driver, winning_team: Team):
        winning_driver_engine = winning_driver.team.engine if winning_driver.team else None
        winning_team_engine = winning_team.engine

        # Deprecate expired contracts
        for team in self.teams:
            team.tick_engine_contract()
            if not team.is_engine_contract_still_valid():
                team.remove_engine()

        # Lock engine-supplier owned teams to their owner's engine
        for team in self.teams:
            if team.owner_type != OwnerType.ENGINE_SUPPLIER or team.owner_engine is None:
                continue
            if team.engine is not team.owner_engine:
                if team.engine is not None:
                    team.engine.remove_team(team)
                team.engine = team.owner_engine
                team.owner_engine.add_team(team)
                team.engine_contract = TeamConstants.ENGINE_OWNER_CONTRACT

        # Championship winner's team and winning team keep their engines
        for team, engine in [
            (winning_driver.team, winning_driver_engine),
            (winning_team, winning_team_engine),
        ]:
            if team is not None and team.engine is None and engine is not None:
                team.engine = engine
                engine.add_team(team)
                team.engine_contract = self._random_contract_years()

        # Engine suppliers restrict supply from teams that finished above them or in top 3
        team_to_pos = {team: pos + 1 for pos, (team, _) in enumerate(sorted_teams)}
        # Maps engine -> highest rival position still blocked (top 3 or anyone above the supplier)
        engine_rival_threshold: dict = {}
        for team in self.teams:
            if team.owner_type == OwnerType.ENGINE_SUPPLIER and team.owner_engine is not None:
                supplier_pos = team_to_pos.get(team, 999)
                engine_rival_threshold[team.owner_engine] = max(3, supplier_pos - 1)

        # Everyone else picks best available engine
        for team, _ in sorted_teams:
            if team.engine is not None:
                continue
            perception = self._compute_engine_perception(team)
            rival_pos = team_to_pos.get(team, 999)
            for engine in perception:
                threshold = engine_rival_threshold.get(engine)
                if (
                    len(engine.teams) < SimulationConstants.MAX_TEAMS_PER_ENGINE
                    and not (threshold is not None and rival_pos <= threshold)
                ):
                    team.engine = engine
                    engine.add_team(team)
                    team.engine_contract = self._random_contract_years()
                    self._emit_event(
                        db, season_num, "engine_deal",
                        f"{team.name} signed a {team.engine_contract}-year engine deal with {engine.name} "
                        f"(power {int(engine.power * 100)}, reliability {int(engine.reliability * 100)}).",
                    )
                    break

        # Fallback: if a team still has no engine (all options were restricted), pick freely
        for team, _ in sorted_teams:
            if team.engine is not None:
                continue
            perception = self._compute_engine_perception(team)
            for engine in perception:
                if len(engine.teams) < SimulationConstants.MAX_TEAMS_PER_ENGINE:
                    team.engine = engine
                    engine.add_team(team)
                    team.engine_contract = self._random_contract_years()
                    self._emit_event(
                        db, season_num, "engine_deal",
                        f"{team.name} signed a {team.engine_contract}-year engine deal with {engine.name} "
                        f"(power {int(engine.power * 100)}, reliability {int(engine.reliability * 100)}).",
                    )
                    break

        # Sync all teams' engine data to DB
        for team in self.teams:
            self._sync_team_to_db(db, team)

    def _check_team_sales(self, db, season_num: int, sorted_teams):
        """Evaluate struggling teams for potential sale and execute any sales."""
        for team in list(self.teams):  # iterate copy; self.teams may change
            if not _is_struggling(team):
                continue
            if random.random() > TeamConstants.SALE_PROBABILITY:
                continue
            buyer_type = _pick_buyer_type(self.teams, self.engines, self.sponsors)
            if buyer_type is None:
                continue
            new_team = self._execute_sale(db, season_num, team, buyer_type)
            # Replace old entry in sorted_teams with new team at same position
            sorted_teams = [
                (new_team, pts) if t is team else (t, pts)
                for t, pts in sorted_teams
            ]
        return sorted_teams

    def _sync_team_to_db(self, db, team: Team) -> None:
        """Persist current in-memory engine/driver/sponsor state to the Team DB row."""
        engine_id = team.engine.db_id if team.engine else None
        sponsor_id = team.sponsor.db_id if team.sponsor else None
        dc = team.driver_contracts
        db.query(m.Team).filter_by(id=team.db_id).update({
            "engine_id": engine_id,
            "engine_contract": team.engine_contract,
            "driver_contract_1": dc[0] if len(dc) > 0 else None,
            "driver_contract_2": dc[1] if len(dc) > 1 else None,
            "chassis": team.chassis,
            "sponsor_id": sponsor_id,
            "sponsor_contract": team.sponsor_contract,
        })

    def _execute_sale(self, db, season_num: int, old_team: Team, buyer_type: str) -> Team:
        """Create a successor team entity and transfer assets."""
        names_dir = self.driver_generator.names_dir

        if buyer_type == OwnerType.ENGINE_SUPPLIER:
            owning_engine_ids = {t.owner_engine.db_id for t in self.teams if t.owner_engine}
            free_engines = [e for e in self.engines if e.db_id not in owning_engine_ids]
            buyer_engine = max(free_engines, key=lambda e: e.power)
            buyer_sponsor = None
            new_name = buyer_engine.name
            new_primary = buyer_engine.color_primary
            new_secondary = buyer_engine.color_secondary
        elif buyer_type == OwnerType.SPONSOR:
            buyer_engine = None
            large_free = [s for s in self.sponsors if s.tier == "large" and s.team is None]
            buyer_sponsor = random.choice(large_free)
            new_name = buyer_sponsor.name
            new_primary = buyer_sponsor.color_primary
            new_secondary = buyer_sponsor.color_secondary
        else:
            buyer_engine = None
            buyer_sponsor = None
            new_name, new_primary, new_secondary, new_nationality = self._generate_individual_team_identity(
                names_dir
            )

        # Mark old team inactive
        db.query(m.Team).filter_by(id=old_team.db_id).update({"is_active": False})

        if buyer_type == OwnerType.ENGINE_SUPPLIER:
            new_nationality = buyer_engine.nationality
        elif buyer_type == OwnerType.SPONSOR:
            new_nationality = buyer_sponsor.nationality

        # Determine finance_base for the new owner type
        if buyer_type == OwnerType.ENGINE_SUPPLIER:
            new_finance_base = TeamConstants.FINANCE_BASE_ENGINE_SUPPLIER
        elif buyer_type == OwnerType.SPONSOR:
            new_finance_base = TeamConstants.FINANCE_BASE_SPONSOR_OWNER
        else:
            new_finance_base = random.randint(
                TeamConstants.FINANCE_BASE_INDIVIDUAL_MIN,
                TeamConstants.FINANCE_BASE_INDIVIDUAL_MAX,
            )

        # Create new DB row
        db_new = m.Team(
            name=new_name,
            nationality=new_nationality,
            color_primary=new_primary,
            color_secondary=new_secondary,
            sponsor_id=None,
            sponsor_contract=None,
            is_active=True,
            owner_type=buyer_type,
            owner_engine_id=buyer_engine.db_id if buyer_engine else None,
            owner_sponsor_id=buyer_sponsor.db_id if buyer_sponsor else None,
            predecessor_team_id=old_team.db_id,
            finance_base=new_finance_base,
        )
        db.add(db_new)
        db.flush()

        # Build new in-memory team
        new_team = Team(
            name=new_name,
            drivers=list(old_team.drivers),
            driver_contracts=list(old_team.driver_contracts),
            chassis=old_team.chassis,
            engine=old_team.engine,
            color_primary=new_primary,
            color_secondary=new_secondary,
            engine_contract=old_team.engine_contract,
            sponsor=old_team.sponsor,
            sponsor_contract=old_team.sponsor_contract,
            db_id=db_new.id,
            nationality=new_nationality,
            owner_type=buyer_type,
            owner_engine=buyer_engine,
            owner_sponsor=buyer_sponsor,
            finance_base=new_finance_base,
        )
        # Generate fresh chiefs for the new ownership era
        def _persist_chief(chief: Chief, team_id: int) -> None:
            db_c = m.TeamChief(
                first_name=chief.first_name,
                last_name=chief.last_name,
                nationality=chief.nationality,
                role=chief.role,
                age=chief.age,
                skill_primary=chief.skill_primary,
                skill_secondary=chief.skill_secondary,
                team_id=team_id,
                contract_years=chief.contract_years,
                retired=False,
            )
            db.add(db_c)
            db.flush()
            chief.db_id = db_c.id
            self.chiefs.append(chief)

        new_owner = self.chief_generator.generate_owner(new_name, new_nationality, buyer_type)
        new_owner.team = new_team
        _persist_chief(new_owner, db_new.id)
        new_team.owner = new_owner

        new_cto = self.chief_generator.generate_cto(new_nationality)
        new_cto.team = new_team
        _persist_chief(new_cto, db_new.id)
        new_team.cto = new_cto

        new_cmo = self.chief_generator.generate_cmo(new_nationality)
        new_cmo.team = new_team
        _persist_chief(new_cmo, db_new.id)
        new_team.cmo = new_cmo

        new_cpo = self.chief_generator.generate_cpo(new_nationality)
        new_cpo.team = new_team
        _persist_chief(new_cpo, db_new.id)
        new_team.cpo = new_cpo

        # Engine lock-in for supplier buyers
        if buyer_type == OwnerType.ENGINE_SUPPLIER:
            if new_team.engine is not None and new_team.engine is not buyer_engine:
                new_team.engine.remove_team(old_team)
            new_team.engine = buyer_engine
            new_team.engine_contract = TeamConstants.ENGINE_OWNER_CONTRACT
            buyer_engine.add_team(new_team)
        elif old_team.engine is not None:
            # Transfer existing engine team reference from old to new
            old_team.engine.remove_team(old_team)
            if new_team.engine is not None:
                new_team.engine.add_team(new_team)

        # Sponsor lock-in for sponsor buyers
        if buyer_type == OwnerType.SPONSOR:
            if old_team.sponsor is not None:
                old_team.sponsor.release_team()
            new_team.sponsor = buyer_sponsor
            new_team.sponsor_contract = TeamConstants.SPONSOR_OWNER_CONTRACT
            buyer_sponsor.assign_team(new_team)
            db.query(m.Team).filter_by(id=db_new.id).update({
                "sponsor_id": buyer_sponsor.db_id,
                "sponsor_contract": TeamConstants.SPONSOR_OWNER_CONTRACT,
            })
        elif old_team.sponsor is not None:
            # Transfer existing sponsor reference
            old_team.sponsor.release_team()
            if new_team.sponsor is not None:
                new_team.sponsor.assign_team(new_team)

        # Update driver back-references
        for drv in new_team.drivers:
            if drv is not None:
                drv.team = new_team

        # Retire old team's chiefs
        season_num_local = season_num  # capture for closure
        for role_attr in ("owner", "cto", "cmo", "cpo"):
            old_chief: Optional[Chief] = getattr(old_team, role_attr)
            if old_chief is not None:
                db.query(m.TeamChief).filter_by(id=old_chief.db_id).update({
                    "retired": True, "retired_season": season_num_local, "team_id": None,
                })
                old_chief.retired = True
                if old_chief in self.chiefs:
                    self.chiefs.remove(old_chief)

        # Swap in self.teams
        self.teams.remove(old_team)
        self.teams.append(new_team)

        if buyer_type == OwnerType.ENGINE_SUPPLIER:
            owner_label = buyer_engine.name
        elif buyer_type == OwnerType.SPONSOR:
            owner_label = buyer_sponsor.name
        else:
            owner_label = "private individual"
        self._emit_event(
            db, season_num, "team_sale",
            f"{old_team.name} was acquired by {owner_label} and rebranded as {new_name}.",
        )
        self._sync_team_to_db(db, new_team)
        return new_team

    def _generate_individual_team_identity(self, names_dir: str):
        """Generate a name, colors, and nationality for an individually-owned team."""
        from sim.seeder import _generate_team_colors
        
        # Pick a random nationality from available directories
        try:
            available_nationalities = [
                d for d in os.listdir(names_dir)
                if os.path.isdir(os.path.join(names_dir, d))
            ]
        except OSError:
            available_nationalities = []
        
        chosen_nationality = random.choice(available_nationalities) if available_nationalities else None
        
        # Generate name from chosen nationality
        if chosen_nationality:
            nat_dir = os.path.join(names_dir, chosen_nationality)
            last_path = os.path.join(nat_dir, "last.txt")
            try:
                with open(last_path) as f:
                    surnames = [s.strip() for s in f if s.strip()]
                name = random.choice(surnames)
            except OSError:
                name = "Racing"
        else:
            name = "Racing"

        used_hues: list[float] = []
        primary, secondary = _generate_team_colors(used_hues)
        return name, primary, secondary, chosen_nationality

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    def _compute_finance_level(
        self,
        team: Team,
        prev_drv_champ_team: Optional[Team],
        prev_team_champ: Optional[Team],
    ) -> int:
        finance = team.finance_base
        if team.sponsor:
            sponsor_bonus = {"small": 1, "medium": 2, "large": 3}.get(team.sponsor.tier, 0)
            finance += sponsor_bonus
        if prev_drv_champ_team is team:
            finance += 1
        if prev_team_champ is team:
            finance += 1
        return finance

    def _compute_driver_utility(self, driver: Driver, team: Team, finance: int, prev_team: Optional[Team]) -> float:
        loyalty_score = 1.0 if (prev_team is not None and prev_team is team) else 0.0
        greed_score = finance / 10.0
        ambition_score = team.avg_skill_100 / 100.0
        return (
            DriverConstants.LOYALTY_WEIGHT * driver.loyalty * loyalty_score
            + DriverConstants.GREED_WEIGHT * driver.greed * greed_score
            + DriverConstants.AMBITION_WEIGHT * driver.ambition * ambition_score
        )

    def _match_drivers_to_teams(
        self,
        db,
        season_num: int,
        sorted_teams,
        prev_drv_champ_team: Optional[Team],
        prev_team_champ: Optional[Team],
    ) -> None:
        """Two-sided matching: teams propose to drivers; drivers tentatively accept best offer."""
        # Compute finance levels for all teams
        finance_by_team: dict[int, int] = {
            id(team): self._compute_finance_level(team, prev_drv_champ_team, prev_team_champ)
            for team, _ in sorted_teams
        }

        # Track each free driver's previous team (before contracts were released this window)
        prev_team_by_driver: dict[int, Optional[Team]] = {
            id(d): d.team for d in self.drivers  # team is None for truly free agents at this point
        }

        # Build team preference lists (scouting-weighted, computed once per team)
        team_prefs: dict[int, List[Driver]] = {}
        for team, _ in sorted_teams:
            open_seats = sum(1 for d in team.drivers if d is None)
            if open_seats > 0:
                team_prefs[id(team)] = self._compute_driver_perception(team)

        # Track tentative assignments: driver -> (team, seat_index)
        tentative: dict[int, Tuple[Team, int]] = {}  # driver id -> (team, seat_idx)
        # Track which drivers each team has already proposed to and been rejected by
        rejected_by_team: dict[int, set] = {id(team): set() for team, _ in sorted_teams}

        teams_with_seats = [team for team, _ in sorted_teams if any(d is None for d in team.drivers)]

        max_rounds = len(self.drivers) + 1
        for _ in range(max_rounds):
            if not teams_with_seats:
                break

            # Each team with open seats makes one offer to its top unrejected free driver
            offers: dict[int, list] = {}  # driver id -> list of (utility, team, seat_idx)
            for team in list(teams_with_seats):
                # Count open seats not yet tentatively filled
                tentatively_filled = sum(
                    1 for d_id, (t, _) in tentative.items() if t is team
                )
                open_seats = sum(1 for d in team.drivers if d is None) - tentatively_filled
                if open_seats <= 0:
                    continue

                prefs = team_prefs.get(id(team), [])
                rejected = rejected_by_team[id(team)]
                # Find seat indices that are empty and not tentatively filled
                already_filling = {
                    seat for d_id, (t, seat) in tentative.items() if t is team
                }
                open_seat_idxs = [
                    i for i, d in enumerate(team.drivers)
                    if d is None and i not in already_filling
                ]

                # Prevent offering the same driver to multiple seats in one round,
                # which would cause the algorithm to oscillate without converging.
                offered_this_round: set[int] = set()
                for seat_idx in open_seat_idxs:
                    for candidate in prefs:
                        if id(candidate) in rejected:
                            continue
                        if candidate.team is not None:
                            continue  # already placed
                        if id(candidate) in offered_this_round:
                            continue  # already offered to this driver for another seat
                        # Make offer
                        finance = finance_by_team[id(team)]
                        prev_team = prev_team_by_driver.get(id(candidate))
                        utility = self._compute_driver_utility(candidate, team, finance, prev_team)
                        offers.setdefault(id(candidate), []).append((utility, team, seat_idx))
                        offered_this_round.add(id(candidate))
                        break

            if not offers:
                break  # no progress possible

            # Each driver with offers tentatively accepts the best one, rejects the rest
            for drv_id, offer_list in offers.items():
                driver = next(d for d in self.drivers if id(d) == drv_id)
                best = max(offer_list, key=lambda x: x[0])
                _, best_team, best_seat = best

                # If driver already had a tentative assignment to a worse team, release it
                if drv_id in tentative:
                    old_team, _ = tentative[drv_id]
                    if old_team is not best_team:
                        rejected_by_team[id(old_team)].add(drv_id)

                tentative[drv_id] = (best_team, best_seat)

                # Reject all other offers
                for utility, team, seat_idx in offer_list:
                    if team is not best_team:
                        rejected_by_team[id(team)].add(drv_id)

            # Recompute which teams still have unfilled open seats
            teams_with_seats = []
            for team, _ in sorted_teams:
                tentatively_filled = sum(1 for _, (t, _) in tentative.items() if t is team)
                open_seats = sum(1 for d in team.drivers if d is None) - tentatively_filled
                if open_seats > 0:
                    # Check there are still free drivers left to offer
                    rejected = rejected_by_team[id(team)]
                    has_candidates = any(
                        id(d) not in rejected and d.team is None
                        for d in self.drivers
                    )
                    if has_candidates:
                        teams_with_seats.append(team)

        # FALLBACK: Greedy matching for any remaining unfilled seats
        # This handles cases where the two-sided matching exhausted all preferences
        remaining_seats: dict[int, list] = {}  # team id -> list of (seat_idx, team_obj)
        for team, _ in sorted_teams:
            tentatively_filled = {
                seat for d_id, (t, seat) in tentative.items() if t is team
            }
            for i, d in enumerate(team.drivers):
                if d is None and i not in tentatively_filled:
                    remaining_seats.setdefault(id(team), []).append((i, team))

        free_drivers = [d for d in self.drivers if d.team is None and id(d) not in tentative]
        
        if remaining_seats and free_drivers:
            # Sort by team preference (best teams pick first)
            for team, _ in sorted_teams:
                if id(team) not in remaining_seats:
                    continue
                for seat_idx, _ in remaining_seats[id(team)]:
                    if not free_drivers:
                        break
                    # Pick best available driver quickly
                    driver = free_drivers.pop(0)
                    tentative[id(driver)] = (team, seat_idx)

        # Finalise all tentative assignments
        for drv_id, (team, seat_idx) in tentative.items():
            driver = next(d for d in self.drivers if id(d) == drv_id)
            team.drivers[seat_idx] = driver
            team.driver_contracts[seat_idx] = self._random_contract_years()
            driver.team = team
            finance = finance_by_team[id(team)]
            prev_team = prev_team_by_driver.get(id(driver))
            stayed = prev_team is team
            trait_note = (
                f"loyalty {int(driver.loyalty * 100)}, "
                f"greed {int(driver.greed * 100)}, "
                f"ambition {int(driver.ambition * 100)}"
            )
            verb = "re-signed with" if stayed else "joined"
            self._emit_event(
                db, season_num, "driver_transfer",
                f"{driver.name} {driver.flag} (skill {driver.skill_100}) "
                f"{verb} {team.name} on a {team.driver_contracts[seat_idx]}-year contract "
                f"[{trait_note}].",
            )

    def _compute_driver_perception(self, team: Team) -> List[Driver]:
        free_drivers = [d for d in self.drivers if d.team is None]
        factor = SimulationConstants.SCOUTING_TRUE_FACTOR
        scouting = factor + team.cpo_scouting * (1 - factor)
        scored = [
            (d.skill * scouting + random.random() * (1 - scouting), i, d)
            for i, d in enumerate(free_drivers)
        ]
        return [d for _, _i, d in sorted(scored, reverse=True)]

    def _compute_engine_perception(self, team: Team) -> List[Engine]:
        factor = SimulationConstants.SCOUTING_TRUE_FACTOR
        scouting = factor + team.cto_eng_scouting * (1 - factor)
        scored = [
            (e.value * scouting + random.random() * (1 - scouting), i, e)
            for i, e in enumerate(self.engines)
        ]
        return [e for _, _i, e in sorted(scored, reverse=True)]

    def _retire_driver(self, db, driver: Driver, season_num: int):
        db.query(m.Driver).filter_by(id=driver.db_id).update({
            "retired": True,
            "retired_season": season_num,
        })
        self.drivers.remove(driver)
        # Generate a replacement
        track_ids = [t.db_id for t in self.tracks if t.db_id]
        new_driver = self.driver_generator.generate_driver(track_ids=track_ids)
        db_driver = m.Driver(
            first_name=new_driver.first_name,
            last_name=new_driver.last_name,
            nationality=new_driver.nationality,
            age=new_driver.age,
            skill=new_driver.skill,
            top_skill=new_driver.top_skill,
            liked_tracks=",".join(str(i) for i in new_driver.liked_track_ids) if new_driver.liked_track_ids else None,
            disliked_tracks=",".join(str(i) for i in new_driver.disliked_track_ids) if new_driver.disliked_track_ids else None,
        )
        db.add(db_driver)
        db.flush()
        new_driver.db_id = db_driver.id
        self.drivers.append(new_driver)
        self._emit_event(
            db, season_num, "driver_debut",
            f"{new_driver.name} {new_driver.flag} (age {new_driver.age}) entered the driver pool.",
        )

    @staticmethod
    def _random_sponsor_contract(tier: str) -> int:
        if tier == "large":
            return random.randint(4, 6)
        elif tier == "medium":
            return random.randint(3, 5)
        else:
            return random.randint(2, 4)

    @staticmethod
    def _random_contract_years() -> int:
        r = random.random()
        if r < 0.02:
            return 2
        elif r < 0.2:
            return 3
        elif r < 0.8:
            return 4
        elif r < 0.95:
            return 5
        else:
            return 6
