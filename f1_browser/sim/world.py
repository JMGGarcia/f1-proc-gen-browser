from __future__ import annotations

import os
import random
from typing import List, Optional, Tuple

from db import models as m
from sim.constants import DriverConstants, PointsSystem, SimulationConstants, TeamConstants
from sim.drivers import Driver, DriverGenerator
from sim.season import Season
from sim.sponsors import Sponsor
from sim.teams import Direction, Engine, OwnerType, Team


def _is_struggling(team: Team) -> bool:
    """Return True if a team has finished outside the top STRUGGLING_THRESHOLD in 4 of the last 5 seasons."""
    ph = team.direction.position_history
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
    ):
        self.tracks = tracks
        self.engines = engines
        self.teams = teams
        self.drivers = drivers
        self.driver_generator = driver_generator
        self.n_seasons = n_seasons
        self.sponsors: List[Sponsor] = sponsors or []

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
        sorted_teams = self._check_team_sales(db, season_num, sorted_teams)
        self._age_drivers(db, season_num)
        self._tweak_chassis_engine(db, season_num, season_id)
        self._teams_pick_engines(db, season_num, sorted_teams, winning_driver, winning_team)
        self._teams_pick_sponsors(db, season_num, sorted_teams)
        self._teams_pick_drivers(db, season_num, sorted_teams, winning_driver, winning_team)
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
                direction_development=team.direction.development,
                direction_scouting=team.direction.scouting,
                direction_eng_scouting=team.direction.eng_scouting,
                direction_years=team.direction.years,
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

    def _update_directions(self, db, season_num: int, sorted_teams):
        total_teams = len(self.teams)
        for idx, (team, _) in enumerate(sorted_teams, 1):
            old_stats = team.direction.get_stats()
            changed = team.direction.yearly_update(idx, total_teams)
            if changed:
                # Apply direction floor for financially-backed owners
                if team.owner_type in (OwnerType.ENGINE_SUPPLIER, OwnerType.SPONSOR):
                    floor = TeamConstants.DIRECTION_OWNER_FLOOR
                    team.direction.development = max(floor, team.direction.development)
                    team.direction.scouting = max(floor, team.direction.scouting)
                    team.direction.eng_scouting = max(floor, team.direction.eng_scouting)

                new_stats = team.direction.get_stats()
                self._emit_event(
                    db, season_num, "direction_change",
                    f"{team.name} appointed a new team principal. "
                    f"Management skill changed from {old_stats[0]} to {new_stats[0]}.",
                )
                # Engine-supplier owned teams don't lose their engine on principal change
                if team.owner_type != OwnerType.ENGINE_SUPPLIER:
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

            # Engine supplier ownership bonus: extra investment in development
            owns_a_team = any(
                t.owner_type == OwnerType.ENGINE_SUPPLIER and t.owner_engine is engine
                for t in engine.teams
            )
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

    def _execute_sale(self, db, season_num: int, old_team: Team, buyer_type: str) -> Team:
        """Create a successor team entity and transfer assets."""
        names_dir = self.driver_generator.names_dir

        if buyer_type == OwnerType.ENGINE_SUPPLIER:
            owning_engine_ids = {t.owner_engine.db_id for t in self.teams if t.owner_engine}
            free_engines = [e for e in self.engines if e.db_id not in owning_engine_ids]
            buyer_engine = random.choice(free_engines)
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
        # Fresh direction, cleared history (new ownership era)
        new_team.direction = Direction()
        new_team.direction.position_history = []
        new_team.direction.years = 0

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

                for seat_idx in open_seat_idxs:
                    for candidate in prefs:
                        if id(candidate) in rejected:
                            continue
                        if candidate.team is not None:
                            continue  # already placed
                        # Make offer
                        finance = finance_by_team[id(team)]
                        prev_team = prev_team_by_driver.get(id(candidate))
                        utility = self._compute_driver_utility(candidate, team, finance, prev_team)
                        offers.setdefault(id(candidate), []).append((utility, team, seat_idx))
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
