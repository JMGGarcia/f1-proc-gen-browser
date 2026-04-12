"""OffSeasonManager: all post-season mutation logic extracted from WorldRunner."""
from __future__ import annotations

import os
import random
from typing import List, Optional, Tuple

from db import models as m
from sim.constants import (
    DriverConstants, EventType, SimulationConstants,
    SponsorConstants, TeamConstants, WorldConstants,
)
from sim.countries import get_all_countries
from sim.db_writers import emit_event, sync_team_to_db
from sim.drivers import Driver, DriverGenerator
from sim.teams import Chief, ChiefGenerator, ChiefRole, Engine, OwnerType, Team


def _is_struggling(team: Team) -> bool:
    """Return True if a team has finished outside the top STRUGGLING_THRESHOLD in 4 of the last 5 seasons."""
    ph = team.position_history
    if len(ph) < SimulationConstants.HISTORY_YEARS:
        return False
    bad = sum(1 for p in ph[-SimulationConstants.HISTORY_YEARS:] if p > TeamConstants.STRUGGLING_THRESHOLD)
    return bad >= 4


def _pick_buyer_type(teams: List[Team], engines: List[Engine], sponsors) -> Optional[str]:
    """Pick a buyer type based on weights, respecting ownership caps."""
    engine_owner_count = sum(1 for t in teams if t.owner_type == OwnerType.ENGINE_SUPPLIER)
    sponsor_owner_count = sum(1 for t in teams if t.owner_type == OwnerType.SPONSOR)

    weights = dict(TeamConstants.BUYER_WEIGHTS)
    if engine_owner_count >= TeamConstants.MAX_ENGINE_SUPPLIER_OWNERS:
        weights[OwnerType.ENGINE_SUPPLIER] = 0
    if sponsor_owner_count >= TeamConstants.MAX_SPONSOR_OWNERS:
        weights[OwnerType.SPONSOR] = 0

    owning_engine_ids = {t.owner_engine.db_id for t in teams if t.owner_engine}
    free_owner_engines = [e for e in engines if e.db_id not in owning_engine_ids]
    if not free_owner_engines:
        weights[OwnerType.ENGINE_SUPPLIER] = 0

    free_large_sponsors = [s for s in sponsors if s.tier == "large" and s.team is None]
    if not free_large_sponsors:
        weights[OwnerType.SPONSOR] = 0

    total = sum(weights.values())
    if total == 0:
        return None

    btypes = list(weights.keys())
    bweights = list(weights.values())
    return random.choices(btypes, weights=bweights, k=1)[0]


class OffSeasonManager:
    def __init__(
        self,
        teams: List[Team],
        drivers: List[Driver],
        engines: List[Engine],
        sponsors,
        chiefs: List[Chief],
        tracks,
        driver_generator: DriverGenerator,
        chief_generator: ChiefGenerator,
    ):
        # All shared list references — mutations here are visible to WorldRunner
        self.teams = teams
        self.drivers = drivers
        self.engines = engines
        self.sponsors = sponsors
        self.chiefs = chiefs
        self.tracks = tracks
        self.driver_generator = driver_generator
        self.chief_generator = chief_generator

    # ------------------------------------------------------------------ #
    # Single entry point                                                   #
    # ------------------------------------------------------------------ #

    def run_offseason(
        self,
        db,
        season_num: int,
        season_id: int,
        sorted_teams,
        winning_driver: Driver,
        winning_team: Team,
    ) -> None:
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

    # ------------------------------------------------------------------ #
    # Position / Chiefs                                                    #
    # ------------------------------------------------------------------ #

    def _update_position_history(self, sorted_teams) -> None:
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
        emit_event(
            db, season_num, EventType.CHIEF_DEBUT,
            f"{new_chief.first_name} {new_chief.last_name} "
            f"(age {new_chief.age}) entered the {new_chief.role.upper()} pool.",
        )

    def _update_chiefs(self, db, season_num: int, sorted_teams) -> None:
        """Age all chiefs, tick skill updates, handle owner succession."""
        for chief in list(self.chiefs):
            chief.age += 1
            chief.yearly_skill_update()
            db.query(m.TeamChief).filter_by(id=chief.db_id).update({
                "age": chief.age,
                "skill_primary": chief.skill_primary,
                "skill_secondary": chief.skill_secondary,
            })
            # Free-agent non-owner retirement
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
                emit_event(
                    db, season_num, EventType.CHIEF_SUCCESSION,
                    f"{team.name}: {successor.first_name} {successor.last_name} succeeded "
                    f"{chief.first_name} {chief.last_name} as team owner (age {chief.age}).",
                )

    def _tick_chief_contracts(self, db, season_num: int) -> None:
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
                    emit_event(
                        db, season_num, EventType.CHIEF_FREE_AGENT,
                        f"{chief.name} ({chief.role.upper()}) is now a free agent.",
                    )

    def _match_chiefs_to_teams(self, db, season_num: int, sorted_teams) -> None:
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
                    # Safety net: pool unexpectedly empty — generate on demand
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
                emit_event(
                    db, season_num, EventType.CHIEF_SIGNING,
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

    # ------------------------------------------------------------------ #
    # Drivers                                                              #
    # ------------------------------------------------------------------ #

    def _age_drivers(self, db, season_num: int) -> None:
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
            emit_event(
                db, season_num, EventType.DRIVER_RETIREMENT,
                f"{driver.name} {driver.flag} retired from racing at age {driver.age} "
                f"with a peak skill of {driver.top_skill_100}.",
            )
            self._retire_driver(db, driver, season_num)

    def _take_driver_from_team(self, db, team: Team, idx: int, season_num: int) -> None:
        driver = team.release_driver(idx)
        if driver is None:
            return

        retire = driver.age > DriverConstants.RETIREMENT_AGE or (
            driver.age > DriverConstants.EARLY_RETIREMENT_AGE
            and random.random() < DriverConstants.EARLY_RETIREMENT_CHANCE
        )
        if retire:
            emit_event(
                db, season_num, EventType.DRIVER_RETIREMENT,
                f"{driver.name} {driver.flag} retired from racing at age {driver.age} "
                f"with a peak skill of {driver.top_skill_100}.",
            )
            self._retire_driver(db, driver, season_num)

    def _teams_pick_drivers(
        self,
        db,
        season_num: int,
        sorted_teams,
        winning_driver: Driver,
        winning_team: Team,
    ) -> None:
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
                if driver.team is not None:
                    print(
                        f"  [ERROR] Season {season_num}: {driver.name} is FREE but driver.team = {driver.team.name}",
                        flush=True,
                    )
                    driver.team = None  # Force clear it

        # Sync all teams' driver contract data to DB
        for team in self.teams:
            sync_team_to_db(db, team)

    def _match_drivers_to_teams(
        self,
        db,
        season_num: int,
        sorted_teams,
        prev_drv_champ_team: Optional[Team],
        prev_team_champ: Optional[Team],
    ) -> None:
        """Two-sided matching: teams propose to drivers; drivers tentatively accept best offer."""
        finance_by_team: dict[int, int] = {
            id(team): self._compute_finance_level(team, prev_drv_champ_team, prev_team_champ)
            for team, _ in sorted_teams
        }

        prev_team_by_driver: dict[int, Optional[Team]] = {
            id(d): d.team for d in self.drivers
        }

        team_prefs: dict[int, List[Driver]] = {}
        for team, _ in sorted_teams:
            open_seats = sum(1 for d in team.drivers if d is None)
            if open_seats > 0:
                team_prefs[id(team)] = self._compute_driver_perception(team)

        tentative: dict[int, Tuple[Team, int]] = {}
        rejected_by_team: dict[int, set] = {id(team): set() for team, _ in sorted_teams}
        teams_with_seats = [team for team, _ in sorted_teams if any(d is None for d in team.drivers)]

        max_rounds = len(self.drivers) + 1
        for _ in range(max_rounds):
            if not teams_with_seats:
                break

            offers: dict[int, list] = {}
            for team in list(teams_with_seats):
                tentatively_filled = sum(1 for d_id, (t, _) in tentative.items() if t is team)
                open_seats = sum(1 for d in team.drivers if d is None) - tentatively_filled
                if open_seats <= 0:
                    continue

                prefs = team_prefs.get(id(team), [])
                rejected = rejected_by_team[id(team)]
                already_filling = {seat for d_id, (t, seat) in tentative.items() if t is team}
                open_seat_idxs = [
                    i for i, d in enumerate(team.drivers)
                    if d is None and i not in already_filling
                ]

                offered_this_round: set[int] = set()
                for seat_idx in open_seat_idxs:
                    for candidate in prefs:
                        if id(candidate) in rejected:
                            continue
                        if candidate.team is not None:
                            continue
                        if id(candidate) in offered_this_round:
                            continue
                        finance = finance_by_team[id(team)]
                        prev_team = prev_team_by_driver.get(id(candidate))
                        utility = self._compute_driver_utility(candidate, team, finance, prev_team)
                        offers.setdefault(id(candidate), []).append((utility, team, seat_idx))
                        offered_this_round.add(id(candidate))
                        break

            if not offers:
                break

            for drv_id, offer_list in offers.items():
                driver = next(d for d in self.drivers if id(d) == drv_id)
                best = max(offer_list, key=lambda x: x[0])
                _, best_team, best_seat = best

                if drv_id in tentative:
                    old_t, _ = tentative[drv_id]
                    if old_t is not best_team:
                        rejected_by_team[id(old_t)].add(drv_id)

                tentative[drv_id] = (best_team, best_seat)

                for utility, team, seat_idx in offer_list:
                    if team is not best_team:
                        rejected_by_team[id(team)].add(drv_id)

            teams_with_seats = []
            for team, _ in sorted_teams:
                tentatively_filled = sum(1 for _, (t, _) in tentative.items() if t is team)
                open_seats = sum(1 for d in team.drivers if d is None) - tentatively_filled
                if open_seats > 0:
                    rejected = rejected_by_team[id(team)]
                    has_candidates = any(
                        id(d) not in rejected and d.team is None
                        for d in self.drivers
                    )
                    if has_candidates:
                        teams_with_seats.append(team)

        # FALLBACK: Greedy matching for remaining unfilled seats
        remaining_seats: dict[int, list] = {}
        for team, _ in sorted_teams:
            tentatively_filled = {seat for d_id, (t, seat) in tentative.items() if t is team}
            for i, d in enumerate(team.drivers):
                if d is None and i not in tentatively_filled:
                    remaining_seats.setdefault(id(team), []).append((i, team))

        free_drivers = [d for d in self.drivers if d.team is None and id(d) not in tentative]

        if remaining_seats and free_drivers:
            for team, _ in sorted_teams:
                if id(team) not in remaining_seats:
                    continue
                for seat_idx, _ in remaining_seats[id(team)]:
                    if not free_drivers:
                        break
                    driver = free_drivers.pop(0)
                    tentative[id(driver)] = (team, seat_idx)

        # Finalise all tentative assignments
        for drv_id, (team, seat_idx) in tentative.items():
            driver = next(d for d in self.drivers if id(d) == drv_id)
            team.assign_driver(driver, seat_idx)
            team.driver_contracts[seat_idx] = self._random_contract_years()
            finance = finance_by_team[id(team)]
            prev_team = prev_team_by_driver.get(id(driver))
            stayed = prev_team is team
            trait_note = (
                f"loyalty {int(driver.loyalty * 100)}, "
                f"greed {int(driver.greed * 100)}, "
                f"ambition {int(driver.ambition * 100)}"
            )
            verb = "re-signed with" if stayed else "joined"
            emit_event(
                db, season_num, EventType.DRIVER_TRANSFER,
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

    def _compute_driver_utility(
        self,
        driver: Driver,
        team: Team,
        finance: int,
        prev_team: Optional[Team],
    ) -> float:
        loyalty_score = 1.0 if (prev_team is not None and prev_team is team) else 0.0
        greed_score = finance / 10.0
        ambition_score = team.avg_skill_100 / 100.0
        return (
            DriverConstants.LOYALTY_WEIGHT * driver.loyalty * loyalty_score
            + DriverConstants.GREED_WEIGHT * driver.greed * greed_score
            + DriverConstants.AMBITION_WEIGHT * driver.ambition * ambition_score
        )

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

    def _retire_driver(self, db, driver: Driver, season_num: int) -> None:
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
        emit_event(
            db, season_num, EventType.DRIVER_DEBUT,
            f"{new_driver.name} {new_driver.flag} (age {new_driver.age}) entered the driver pool.",
        )

    def _tweak_driver_form(self) -> None:
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

    # ------------------------------------------------------------------ #
    # Engines                                                              #
    # ------------------------------------------------------------------ #

    def _tweak_chassis_engine(self, db, season_num: int, season_id: int) -> None:
        revolution = random.random() < SimulationConstants.REVOLUTION_PROBABILITY
        random_factor = WorldConstants.CHASSIS_ENGINE_RANDOM_FACTOR

        if revolution:
            emit_event(
                db, season_num, EventType.FORMULA_REVOLUTION,
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

            if owns_a_team:
                engine.power = min(1.0, engine.power + TeamConstants.ENGINE_OWNER_POWER_BONUS)

            if revolution:
                engine.reliability -= (
                    random.random() * WorldConstants.ENGINE_RELIABILITY_REVOLUTION_DELTA_RANGE
                    + WorldConstants.ENGINE_RELIABILITY_REVOLUTION_DELTA_MIN
                )
            engine.reliability += random.random() * 0.1 + 0.05
            engine.reliability = min(
                SimulationConstants.MAXIMUM_RELIABILITY,
                max(SimulationConstants.MINIMUM_RELIABILITY, engine.reliability),
            )
            engine.update_value()

            db.query(m.Engine).filter_by(id=engine.db_id).update({
                "power": engine.power,
                "reliability": engine.reliability,
                "value": engine.value,
            })

    def _teams_pick_engines(
        self,
        db,
        season_num: int,
        sorted_teams,
        winning_driver: Driver,
        winning_team: Team,
    ) -> None:
        winning_driver_engine = winning_driver.team.engine if winning_driver.team else None
        winning_team_engine = winning_team.engine

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

        team_to_pos = {team: pos + 1 for pos, (team, _) in enumerate(sorted_teams)}
        engine_rival_threshold: dict = {}
        for team in self.teams:
            if team.owner_type == OwnerType.ENGINE_SUPPLIER and team.owner_engine is not None:
                supplier_pos = team_to_pos.get(team, WorldConstants.FALLBACK_POSITION)
                engine_rival_threshold[team.owner_engine] = max(3, supplier_pos - 1)

        for team, _ in sorted_teams:
            if team.engine is not None:
                continue
            perception = self._compute_engine_perception(team)
            rival_pos = team_to_pos.get(team, WorldConstants.FALLBACK_POSITION)
            for engine in perception:
                threshold = engine_rival_threshold.get(engine)
                if (
                    len(engine.teams) < SimulationConstants.MAX_TEAMS_PER_ENGINE
                    and not (threshold is not None and rival_pos <= threshold)
                ):
                    team.engine = engine
                    engine.add_team(team)
                    team.engine_contract = self._random_contract_years()
                    emit_event(
                        db, season_num, EventType.ENGINE_DEAL,
                        f"{team.name} signed a {team.engine_contract}-year engine deal with {engine.name} "
                        f"(power {int(engine.power * 100)}, reliability {int(engine.reliability * 100)}).",
                    )
                    break

        # Fallback: pick freely if still no engine
        for team, _ in sorted_teams:
            if team.engine is not None:
                continue
            perception = self._compute_engine_perception(team)
            for engine in perception:
                if len(engine.teams) < SimulationConstants.MAX_TEAMS_PER_ENGINE:
                    team.engine = engine
                    engine.add_team(team)
                    team.engine_contract = self._random_contract_years()
                    emit_event(
                        db, season_num, EventType.ENGINE_DEAL,
                        f"{team.name} signed a {team.engine_contract}-year engine deal with {engine.name} "
                        f"(power {int(engine.power * 100)}, reliability {int(engine.reliability * 100)}).",
                    )
                    break

        for team in self.teams:
            sync_team_to_db(db, team)

    def _compute_engine_perception(self, team: Team) -> List[Engine]:
        factor = SimulationConstants.SCOUTING_TRUE_FACTOR
        scouting = factor + team.cto_eng_scouting * (1 - factor)
        scored = [
            (e.value * scouting + random.random() * (1 - scouting), i, e)
            for i, e in enumerate(self.engines)
        ]
        return [e for _, _i, e in sorted(scored, reverse=True)]

    # ------------------------------------------------------------------ #
    # Sponsors                                                             #
    # ------------------------------------------------------------------ #

    def _teams_pick_sponsors(self, db, season_num: int, sorted_teams) -> None:
        if not self.sponsors:
            return

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
            if ((r1-r2)**2 + (g1-g2)**2 + (b1-b2)**2) ** 0.5 <= SponsorConstants.COLOR_DISTANCE_THRESHOLD:
                score += 1
            if team.engine and team.engine.rgb_primary:
                r3, g3, b3 = team.engine.rgb_primary
                if ((r3-r2)**2 + (g3-g2)**2 + (b3-b2)**2) ** 0.5 <= SponsorConstants.COLOR_DISTANCE_THRESHOLD:
                    score += 1
            return score

        position_map_pre = {team: pos for pos, (team, _) in enumerate(sorted_teams, 1)}
        total_teams = len(self.teams)
        expiring_map: dict = {}

        for team in self.teams:
            if not team.sponsor:
                continue
            team.tick_sponsor_contract()
            if team.is_sponsor_contract_still_valid():
                continue
            # Sponsor-owned teams always keep their owner sponsor
            if team.owner_type == OwnerType.SPONSOR and team.owner_sponsor is not None:
                new_contract = TeamConstants.SPONSOR_OWNER_CONTRACT
                team.sponsor_contract = new_contract
                db.query(m.Team).filter_by(id=team.db_id).update({"sponsor_contract": new_contract})
                continue
            expiring_sponsor = team.sponsor
            pos = position_map_pre.get(team, total_teams)

            if pos <= max(5, total_teams // 2):
                max_eligible = "large"
            elif pos <= max(total_teams - 3, total_teams * 7 // 10):
                max_eligible = "medium"
            else:
                max_eligible = "small"

            tier_rank = {"large": 3, "medium": 2, "small": 1}
            sponsor_still_fits = tier_rank[expiring_sponsor.tier] <= tier_rank[max_eligible]

            if sponsor_still_fits and random.random() < SponsorConstants.RENEWAL_PROBABILITY:
                new_contract = self._random_sponsor_contract(expiring_sponsor.tier)
                team.sponsor_contract = new_contract
                db.query(m.Team).filter_by(id=team.db_id).update({"sponsor_contract": new_contract})
                emit_event(
                    db, season_num, EventType.ENGINE_DEAL,
                    f"{team.name} renewed their sponsorship deal with {expiring_sponsor.name} "
                    f"for {new_contract} more year(s).",
                )
            else:
                emit_event(
                    db, season_num, EventType.ENGINE_DEAL,
                    f"{team.name}'s sponsorship deal with {expiring_sponsor.name} has ended.",
                )
                expiring_map[team] = expiring_sponsor
                team.remove_sponsor()

        for team, _ in sorted_teams:
            if team.sponsor is not None:
                continue
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

            eligible_tiers = ["small"]
            if pos <= max(total_teams - 3, total_teams * 7 // 10):
                eligible_tiers.append("medium")
            if pos <= max(5, total_teams // 2):
                eligible_tiers.append("large")

            tier_order = [t for t in ["large", "medium", "small"] if t in eligible_tiers]
            prev_sponsor = expiring_map.get(team)
            chosen = None

            for tier in tier_order:
                candidates = [s for s in self.sponsors if s.team is None and s.tier == tier]
                if candidates:
                    chosen = max(candidates, key=lambda s: _score_sponsor(s, team, prev_sponsor))
                    break

            if chosen is None:
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
            emit_event(
                db, season_num, EventType.ENGINE_DEAL,
                f"{team.name} signed a {contract}-year sponsorship deal with {chosen.name}.",
            )

    # ------------------------------------------------------------------ #
    # Team sales                                                           #
    # ------------------------------------------------------------------ #

    def _check_team_sales(self, db, season_num: int, sorted_teams):
        """Evaluate struggling teams for potential sale and execute any sales."""
        for team in list(self.teams):
            if not _is_struggling(team):
                continue
            if random.random() > TeamConstants.SALE_PROBABILITY:
                continue
            buyer_type = _pick_buyer_type(self.teams, self.engines, self.sponsors)
            if buyer_type is None:
                continue
            new_team = self._execute_sale(db, season_num, team, buyer_type)
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

        db.query(m.Team).filter_by(id=old_team.db_id).update({"is_active": False})

        if buyer_type == OwnerType.ENGINE_SUPPLIER:
            new_nationality = buyer_engine.nationality
        elif buyer_type == OwnerType.SPONSOR:
            new_nationality = buyer_sponsor.nationality

        if buyer_type == OwnerType.ENGINE_SUPPLIER:
            new_finance_base = TeamConstants.FINANCE_BASE_ENGINE_SUPPLIER
        elif buyer_type == OwnerType.SPONSOR:
            new_finance_base = TeamConstants.FINANCE_BASE_SPONSOR_OWNER
        else:
            new_finance_base = random.randint(
                TeamConstants.FINANCE_BASE_INDIVIDUAL_MIN,
                TeamConstants.FINANCE_BASE_INDIVIDUAL_MAX,
            )

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

        if buyer_type == OwnerType.ENGINE_SUPPLIER:
            if new_team.engine is not None and new_team.engine is not buyer_engine:
                new_team.engine.remove_team(old_team)
            new_team.engine = buyer_engine
            new_team.engine_contract = TeamConstants.ENGINE_OWNER_CONTRACT
            buyer_engine.add_team(new_team)
        elif old_team.engine is not None:
            old_team.engine.remove_team(old_team)
            if new_team.engine is not None:
                new_team.engine.add_team(new_team)

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
            old_team.sponsor.release_team()
            if new_team.sponsor is not None:
                new_team.sponsor.assign_team(new_team)

        # Update driver back-references
        for idx, drv in enumerate(new_team.drivers):
            if drv is not None:
                new_team.assign_driver(drv, idx)

        # Retire old team's chiefs
        for role_attr in ("owner", "cto", "cmo", "cpo"):
            old_chief: Optional[Chief] = getattr(old_team, role_attr)
            if old_chief is not None:
                db.query(m.TeamChief).filter_by(id=old_chief.db_id).update({
                    "retired": True, "retired_season": season_num, "team_id": None,
                })
                old_chief.retired = True
                if old_chief in self.chiefs:
                    self.chiefs.remove(old_chief)

        self.teams.remove(old_team)
        self.teams.append(new_team)

        if buyer_type == OwnerType.ENGINE_SUPPLIER:
            owner_label = buyer_engine.name
        elif buyer_type == OwnerType.SPONSOR:
            owner_label = buyer_sponsor.name
        else:
            owner_label = "private individual"
        emit_event(
            db, season_num, EventType.TEAM_SALE,
            f"{old_team.name} was acquired by {owner_label} and rebranded as {new_name}.",
        )
        sync_team_to_db(db, new_team)
        return new_team

    def _generate_individual_team_identity(self, names_dir: str):
        """Generate a name, colors, and nationality for an individually-owned team."""
        from sim.seeder import _generate_team_colors

        try:
            available_nationalities = [
                d for d in os.listdir(names_dir)
                if os.path.isdir(os.path.join(names_dir, d))
            ]
        except OSError:
            available_nationalities = []

        chosen_nationality = random.choice(available_nationalities) if available_nationalities else None

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
    # Utilities                                                            #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _random_sponsor_contract(tier: str) -> int:
        if tier == "large":
            return random.randint(SponsorConstants.CONTRACT_LARGE_MIN, SponsorConstants.CONTRACT_LARGE_MAX)
        elif tier == "medium":
            return random.randint(SponsorConstants.CONTRACT_MEDIUM_MIN, SponsorConstants.CONTRACT_MEDIUM_MAX)
        else:
            return random.randint(SponsorConstants.CONTRACT_SMALL_MIN, SponsorConstants.CONTRACT_SMALL_MAX)

    @staticmethod
    def _random_contract_years() -> int:
        r = random.random()
        if r < WorldConstants.CONTRACT_YEARS_PROB_2:
            return 2
        elif r < WorldConstants.CONTRACT_YEARS_PROB_3:
            return 3
        elif r < WorldConstants.CONTRACT_YEARS_PROB_4:
            return 4
        elif r < WorldConstants.CONTRACT_YEARS_PROB_5:
            return 5
        else:
            return 6
