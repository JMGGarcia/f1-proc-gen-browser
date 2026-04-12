from __future__ import annotations

import os
import random
from typing import List, Optional, Tuple

from db import models as m
from db.backup import cleanup_backups, get_world_id8, make_backup
from sim.constants import DriverConstants, EventType, PointsSystem, RaceConstants, SimulationConstants, SponsorConstants, TeamConstants, WorldConstants
from sim.countries import get_all_countries
from sim.db_writers import (
    emit_event, sync_team_to_db, write_race_results, write_race_results_for,
    write_one_race, write_season_stats,
)
from sim.driver_events import batch_tick_race_modifiers, tick_race_modifiers
from sim.drivers import Driver, DriverGenerator
from sim.flags import NATIONALITY_FLAGS
from sim.offseason import OffSeasonManager
from sim.race import LapRace
from sim.season import Season
from sim.serializers import (
    serialise_driver_snap, serialise_lap_event, serialise_results, serialise_team_snap,
)
from sim.sponsors import Sponsor
from sim.teams import Chief, ChiefGenerator, ChiefRole, Engine, OwnerType, Team


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
            names_dir=driver_generator.names_dir,
            name_structure=driver_generator.name_structure,
        )
        self.chiefs: List[Chief] = chiefs or []

        self._offseason = OffSeasonManager(
            teams=self.teams,
            drivers=self.drivers,
            engines=self.engines,
            sponsors=self.sponsors,
            chiefs=self.chiefs,
            tracks=self.tracks,
            driver_generator=self.driver_generator,
            chief_generator=self.chief_generator,
        )

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
            payload = serialise_lap_event(
                lap_num, standings, self._tick_season_num,
                self._current_round_num, self._tick_total_rounds, self._current_track,
            )
            self._last_lap_num = lap_num
            self._last_lap_standings = payload["standings"]
            return [payload]
        except StopIteration:
            # All 50 laps done — finalise the race
            results = self._current_lap_race.get_results()

            # Award championship points
            points_table = PointsSystem.RACE_POINTS
            for idx, (driver, time_val) in enumerate(results):
                if time_val != -1.0 and idx < len(points_table):
                    self._tick_season._award_points(driver, points_table[idx])

            write_race_results_for(db, self._current_db_race_id, results)
            self._tick_race_records.append((self._current_track, results))

            # Tick race-based modifiers (expire those that hit 0)
            expired_pairs = tick_race_modifiers(self.drivers, db, self._tick_season_num)
            for driver, mod in expired_pairs:
                self._offseason._expire_modifier(db, driver, mod, self._tick_season_num)

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
                "results": serialise_results(results),
                "driver_standings": serialise_driver_snap(driver_snap),
                "team_standings": serialise_team_snap(team_snap),
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

    def is_race_active(self) -> bool:
        """Return True if a race lap iterator is currently active."""
        return self._current_lap_iter is not None

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
                team.release_driver(0)
                team.release_driver(1)

            for driver_id, team_id in season_driver_team.items():
                driver = driver_map.get(driver_id)
                team = team_map.get(team_id)
                if driver is None or team is None:
                    continue
                if team.drivers[0] is None:
                    team.assign_driver(driver, 0)
                elif team.drivers[1] is None:
                    team.assign_driver(driver, 1)

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

        write_season_stats(db, self.engines, season_id, race_records, sorted_drivers, sorted_teams)
        db.query(m.Season).filter_by(id=season_id).update({"completed": True})
        db.flush()

        # Serialise standings NOW, before off-season mutations change driver.team / team names
        serialised_driver_standings = serialise_driver_snap(sorted_drivers)
        serialised_team_standings = serialise_team_snap(sorted_teams)
        driver_champion_name = f"{sorted_drivers[0][0].first_name} {sorted_drivers[0][0].last_name}"
        driver_champion_pts = sorted_drivers[0][1]
        team_champion_name = sorted_teams[0][0].name
        team_champion_pts = sorted_teams[0][1]

        winning_driver = sorted_drivers[0][0]
        winning_team = sorted_teams[0][0]

        drv_champ_entities = [("driver", winning_driver.db_id)]
        if winning_driver.team:
            drv_champ_entities.append(("team", winning_driver.team.db_id))
        emit_event(
            db, season_num, EventType.DRIVER_CHAMPION,
            f"{driver_champion_name} won the Drivers' Championship with {driver_champion_pts} points!",
            entities=drv_champ_entities,
        )
        emit_event(
            db, season_num, EventType.TEAM_CHAMPION,
            f"{team_champion_name} won the Constructors' Championship with {team_champion_pts} points!",
            entities=[("team", winning_team.db_id)],
        )

        self._offseason.run_offseason(db, season_num, season_id, sorted_teams, winning_driver, winning_team)
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

        write_race_results(db, season_id, race_records)
        write_season_stats(db, self.engines, season_id, race_records, sorted_drivers, sorted_teams)
        db.query(m.Season).filter_by(id=season_id).update({"completed": True})
        db.flush()

        # Batch-tick race-based modifiers for all races in the season
        expired_pairs = batch_tick_race_modifiers(self.drivers, db, len(self.tracks), season_num)
        for driver, mod in expired_pairs:
            self._offseason._expire_modifier(db, driver, mod, season_num)

        winning_driver = sorted_drivers[0][0]
        winning_team = sorted_teams[0][0]

        drv_champ_entities = [("driver", winning_driver.db_id)]
        if winning_driver.team:
            drv_champ_entities.append(("team", winning_driver.team.db_id))
        emit_event(
            db, season_num, EventType.DRIVER_CHAMPION,
            f"{winning_driver.first_name} {winning_driver.last_name} won the Drivers' Championship with {sorted_drivers[0][1]} points!",
            entities=drv_champ_entities,
        )
        emit_event(
            db, season_num, EventType.TEAM_CHAMPION,
            f"{winning_team.name} won the Constructors' Championship with {sorted_teams[0][1]} points!",
            entities=[("team", winning_team.db_id)],
        )

        self._offseason.run_offseason(db, season_num, season_id, sorted_teams, winning_driver, winning_team)
        db.commit()
        make_backup(season_num, db)
        cleanup_backups(get_world_id8(db))
