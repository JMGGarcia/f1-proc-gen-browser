from __future__ import annotations

from typing import Dict, Iterator, List, Tuple

from sim.constants import PointsSystem
from sim.drivers import Driver
from sim.race import LapRace as Race, RacePerformance
from sim.teams import Team
from sim.tracks import Track


# (track, results_list)
RaceRecord = Tuple[Track, List[RacePerformance]]


class Season:
    def __init__(self, tracks: List[Track], teams: List[Team], number: int):
        self.tracks = tracks
        self.teams = teams
        self.number = number
        self.classification_driver: Dict[Driver, int] = {}
        self.classification_team: Dict[Team, int] = {}

        for team in teams:
            self.classification_team[team] = 0
            for driver in team.drivers:
                if driver is not None:
                    self.classification_driver[driver] = 0

    def run(self) -> Tuple[List[RaceRecord], List[Tuple[Driver, int]], List[Tuple[Team, int]]]:
        """Run all races and return (race_records, sorted_drivers, sorted_teams)."""
        points = PointsSystem.RACE_POINTS
        race_records: List[RaceRecord] = []

        for track in self.tracks:
            race = Race(track)
            results = race.perform_race(self.teams)
            race_records.append((track, results))

            for idx, (driver, perf) in enumerate(results):
                if perf == -1.0 or idx >= len(points):
                    continue
                self._award_points(driver, points[idx])

        sorted_drivers = sorted(self.classification_driver.items(), key=lambda x: x[1], reverse=True)
        sorted_teams = sorted(self.classification_team.items(), key=lambda x: x[1], reverse=True)
        return race_records, sorted_drivers, sorted_teams

    def iter_races(self, skip_rounds: set | None = None) -> Iterator[tuple]:
        """Yield (round_num, track, results, driver_snap, team_snap) after each race.
        driver_snap / team_snap are [(obj, pts), ...] sorted descending by points.
        skip_rounds: round numbers already in DB (for mid-season resume); those rounds
        are not simulated and not yielded — classification must be pre-populated."""
        points = PointsSystem.RACE_POINTS
        for round_num, track in enumerate(self.tracks, 1):
            if skip_rounds and round_num in skip_rounds:
                continue
            race = Race(track)
            results = race.perform_race(self.teams)
            for idx, (driver, perf) in enumerate(results):
                if perf != -1.0 and idx < len(points):
                    self._award_points(driver, points[idx])
            driver_snap = sorted(self.classification_driver.items(), key=lambda x: x[1], reverse=True)
            team_snap = sorted(self.classification_team.items(), key=lambda x: x[1], reverse=True)
            yield round_num, track, results, driver_snap, team_snap

    def _award_points(self, driver: Driver, pts: int):
        self.classification_driver[driver] = self.classification_driver.get(driver, 0) + pts
        if driver.team is not None:
            self.classification_team[driver.team] = self.classification_team.get(driver.team, 0) + pts
