from __future__ import annotations

import random
from typing import List, Optional, Tuple

from db import models as m
from sim.constants import DriverConstants, PointsSystem, SimulationConstants
from sim.drivers import Driver, DriverGenerator
from sim.season import Season
from sim.teams import Engine, Team


class WorldRunner:
    def __init__(
        self,
        tracks,
        engines: List[Engine],
        teams: List[Team],
        drivers: List[Driver],
        driver_generator: DriverGenerator,
        n_seasons: int,
    ):
        self.tracks = tracks
        self.engines = engines
        self.teams = teams
        self.drivers = drivers
        self.driver_generator = driver_generator
        self.n_seasons = n_seasons

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

        self._update_directions(db, season_num, sorted_teams)
        self._age_drivers(db, season_num)
        self._tweak_chassis_engine(db, season_num, season_id)
        self._teams_pick_engines(db, season_num, sorted_teams, winning_driver, winning_team)
        self._teams_pick_drivers(db, season_num, sorted_teams)
        self._tweak_driver_form()
        db.commit()

    # ------------------------------------------------------------------ #
    # DB writers                                                           #
    # ------------------------------------------------------------------ #

    def _write_race_results(self, db, season_id: int, race_records):
        points_table = PointsSystem.RACE_POINTS
        for round_num, (track, results) in enumerate(race_records, 1):
            db_race = m.Race(season_id=season_id, track_id=track.db_id, round_number=round_num)
            db.add(db_race)
            db.flush()

            for idx, (driver, perf) in enumerate(results):
                dnf = perf == -1.0
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
                direction_avg=team.direction.avg,
                total_points=team_points.get(team, 0),
                championship_position=pos,
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

    def _update_directions(self, db, season_num: int, sorted_teams):
        total_teams = len(self.teams)
        for idx, (team, _) in enumerate(sorted_teams, 1):
            old_stats = team.direction.get_stats()
            changed = team.direction.yearly_update(idx, total_teams)
            if changed:
                new_stats = team.direction.get_stats()
                self._emit_event(
                    db, season_num, "direction_change",
                    f"{team.name} appointed a new team principal. "
                    f"Management skill changed from {old_stats[0]} to {new_stats[0]}.",
                )
                team.remove_engine()

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
            delta += team.direction.development * SimulationConstants.TEAM_DEVELOPMENT_INFLUENCE
            team.chassis = min(1.0, max(0.0, team.chassis + delta))

        for engine in self.engines:
            if revolution:
                engine.power = (
                    (1 - SimulationConstants.REVOLUTION_EFFECT) * engine.power
                    + SimulationConstants.REVOLUTION_EFFECT * random.random()
                )
            delta = random.random() * random_factor - random_factor / 2
            engine.power = min(1.0, max(0.0, engine.power + delta))

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

    def _teams_pick_drivers(self, db, season_num: int, sorted_teams):
        # Release expired contracts
        for team in self.teams:
            drv1_ok, drv2_ok = team.is_drivers_contracts_still_valid()
            if not drv1_ok:
                self._take_driver_from_team(db, team, 0, season_num)
            if not drv2_ok:
                self._take_driver_from_team(db, team, 1, season_num)

        # Fill empty seats (priority: highest-placed team picks first)
        for team, _ in sorted_teams:
            cached_perception: Optional[List[Driver]] = None
            for idx, driver in enumerate(team.drivers):
                if driver is None:
                    if cached_perception is None:
                        cached_perception = self._compute_driver_perception(team)
                    new_driver = cached_perception.pop(0)
                    team.drivers[idx] = new_driver
                    team.driver_contracts[idx] = self._random_contract_years()
                    new_driver.team = team
                    self._emit_event(
                        db, season_num, "driver_transfer",
                        f"{new_driver.name} {new_driver.flag} (skill {new_driver.skill_100}) "
                        f"joined {team.name} on a {team.driver_contracts[idx]}-year contract.",
                    )

    def _teams_pick_engines(self, db, season_num: int, sorted_teams, winning_driver: Driver, winning_team: Team):
        winning_driver_engine = winning_driver.team.engine if winning_driver.team else None
        winning_team_engine = winning_team.engine

        # Deprecate expired contracts
        for team in self.teams:
            if not team.is_engine_contract_still_valid():
                team.remove_engine()

        # Championship winner's team and winning team keep their engines
        for team, engine in [
            (winning_driver.team, winning_driver_engine),
            (winning_team, winning_team_engine),
        ]:
            if team is not None and team.engine is None and engine is not None:
                team.engine = engine
                engine.add_team(team)
                team.engine_contract = self._random_contract_years()

        # Everyone else picks best available engine
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

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    def _compute_driver_perception(self, team: Team) -> List[Driver]:
        free_drivers = [d for d in self.drivers if d.team is None]
        factor = SimulationConstants.SCOUTING_TRUE_FACTOR
        scouting = factor + team.direction.scouting * (1 - factor)
        scored = [
            (d.skill * scouting + random.random() * (1 - scouting), i, d)
            for i, d in enumerate(free_drivers)
        ]
        return [d for _, _i, d in sorted(scored, reverse=True)]

    def _compute_engine_perception(self, team: Team) -> List[Engine]:
        factor = SimulationConstants.SCOUTING_TRUE_FACTOR
        scouting = factor + team.direction.eng_scouting * (1 - factor)
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
        new_driver = self.driver_generator.generate_driver()
        db_driver = m.Driver(
            first_name=new_driver.first_name,
            last_name=new_driver.last_name,
            nationality=new_driver.nationality,
            age=new_driver.age,
            skill=new_driver.skill,
            top_skill=new_driver.top_skill,
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
