from __future__ import annotations

import random
from dataclasses import dataclass
from typing import List, Optional, Tuple

from sim.constants import RaceConstants, SimulationConstants
from sim.drivers import Driver
from sim.teams import Team
from sim.tracks import Track


# (driver, total_race_time_seconds) — -1.0 means DNF
RacePerformance = Tuple[Driver, float]


@dataclass
class DriverState:
    driver: Driver
    team: Team
    total_time: float           # accumulated race time in seconds
    combined_perf: float        # cached 0–1 performance value
    pit_laps: set               # scheduled pit lap numbers
    dnf: bool = False
    dnf_lap: int = 0
    last_event: Optional[str] = None  # "pit", "minor", "dnf", or None


class LapRace:
    LAPS = 50
    PIT_TIME = 20.0          # seconds lost for a scheduled pit stop
    INCIDENT_PIT_TIME = 30.0  # seconds lost for an incident pit stop

    def __init__(self, track: Track):
        self.track = track
        self._final_results: Optional[List[RacePerformance]] = None

    def _compute_perf(self, team: Team, driver: Driver) -> float:
        doe = self.track.downforce_over_engine
        cod = self.track.car_over_driver
        car_perf = doe * team.chassis + (1 - doe) * team.engine.power
        perf = cod * car_perf + (1 - cod) * driver.effective_skill
        if self.track.db_id and self.track.db_id in driver.liked_track_ids:
            perf += RaceConstants.TRACK_PREFERENCE_BONUS
        elif self.track.db_id and self.track.db_id in driver.disliked_track_ids:
            perf -= RaceConstants.TRACK_PREFERENCE_BONUS
        return perf

    def build_grid(self, teams: List[Team]) -> List[DriverState]:
        """Sort drivers by qualifying performance and assign 0.2 s start gaps."""
        entries = []
        for team in teams:
            for driver in team.drivers:
                if driver is None:
                    continue
                clean_perf = self._compute_perf(team, driver)
                # Qualifying noise — tighter than race randomness
                noise = random.gauss(0, SimulationConstants.RACE_RANDOMNESS * RaceConstants.QUALIFYING_NOISE_FACTOR)
                entries.append((driver, team, clean_perf, clean_perf + noise))

        entries.sort(key=lambda x: x[3], reverse=True)

        states: List[DriverState] = []
        for i, (driver, team, clean_perf, _) in enumerate(entries):
            pit1 = random.randint(*RaceConstants.PIT1_LAP_RANGE)
            pit2 = random.randint(*RaceConstants.PIT2_LAP_RANGE)
            start_time = i * RaceConstants.START_GAP_SECONDS
            states.append(DriverState(
                driver=driver,
                team=team,
                total_time=start_time,
                combined_perf=clean_perf,
                pit_laps={pit1, pit2},
            ))
        return states

    def simulate_lap(self, states: List[DriverState], lap: int) -> None:
        """Advance all active drivers one lap. Mutates states in place."""
        target = self.track.target_lap_time

        for s in states:
            if s.dnf:
                continue
            s.last_event = None

            # Base lap time: worst possible car+driver is LAP_TIME_SPREAD s off target
            lap_time = target + (1 - s.combined_perf) * RaceConstants.LAP_TIME_SPREAD + random.gauss(0, RaceConstants.LAP_TIME_NOISE_STDDEV)
            lap_time = max(lap_time, target * RaceConstants.LAP_TIME_FLOOR_PCT)

            # Scheduled pit stop
            if lap in s.pit_laps:
                lap_time += self.PIT_TIME
                s.last_event = "pit"

            # Per-lap incident probability scales with engine unreliability
            p_incident = max(0.0, 1.0 - s.team.engine.reliability) / RaceConstants.INCIDENT_PROB_DIVISOR
            if random.random() < p_incident:
                if random.random() < RaceConstants.MAJOR_INCIDENT_PROB:   # major incident → DNF
                    s.dnf = True
                    s.dnf_lap = lap
                    s.last_event = "dnf"
                    continue
                else:                        # 60 % chance → minor → extra pit stop
                    lap_time += self.INCIDENT_PIT_TIME
                    s.last_event = "minor"

            s.total_time += lap_time

    @staticmethod
    def _sorted_standings(states: List[DriverState]) -> List[DriverState]:
        active = sorted([s for s in states if not s.dnf], key=lambda s: s.total_time)
        dnf = [s for s in states if s.dnf]
        return active + dnf

    def iter_laps(self, teams: List[Team]):
        """Generator yielding (lap_number, standings) for lap 0 (grid) then laps 1–50."""
        states = self.build_grid(teams)

        # Lap 0: starting grid
        yield 0, self._sorted_standings(states)

        for lap in range(1, self.LAPS + 1):
            self.simulate_lap(states, lap)
            standings = self._sorted_standings(states)
            yield lap, standings

        # Cache final results for get_results()
        self._final_results = [
            (s.driver, -1.0 if s.dnf else s.total_time)
            for s in self._sorted_standings(states)
        ]

    def get_results(self) -> List[RacePerformance]:
        """Final sorted results; valid after iter_laps has been fully consumed."""
        return self._final_results or []

    def perform_race(self, teams: List[Team]) -> List[RacePerformance]:
        """Run full race in one shot. Used by Season.iter_races() / simulate_many."""
        for _ in self.iter_laps(teams):
            pass
        return self.get_results()
