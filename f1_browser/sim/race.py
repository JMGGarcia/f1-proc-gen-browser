from __future__ import annotations

import random
from typing import List, Tuple

from sim.constants import SimulationConstants
from sim.drivers import Driver
from sim.teams import Team
from sim.tracks import Track


# (driver, performance)  — performance == -1 means DNF
RacePerformance = Tuple[Driver, float]


class Race:
    def __init__(self, track: Track):
        self.track = track

    def perform_race(self, teams: List[Team]) -> List[RacePerformance]:
        """Return drivers sorted by finishing position (best first). DNF = -1."""
        performances: List[RacePerformance] = []

        doe = self.track.downforce_over_engine
        cod = self.track.car_over_driver

        for team in teams:
            car_perf = (doe * team.chassis + (1 - doe) * team.engine.power) / 2
            for driver in team.drivers:
                if driver is None:
                    continue
                if random.random() > team.engine.reliability:
                    performances.append((driver, -1.0))
                else:
                    base = cod * car_perf + (1 - cod) * driver.skill
                    rnd = SimulationConstants.RACE_RANDOMNESS
                    perf = random.random() * rnd + (1 - rnd) * base
                    performances.append((driver, perf))

        return sorted(performances, key=lambda x: x[1], reverse=True)
