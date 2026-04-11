from __future__ import annotations

import os
import random
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

from sim.constants import SimulationConstants, TeamConstants
from sim.countries import get_country, get_all_countries

if TYPE_CHECKING:
    from sim.countries import Country

from sim.drivers import Driver, DriverGenerator
from sim.sponsors import Sponsor


class OwnerType:
    INDIVIDUAL = "individual"
    ENGINE_SUPPLIER = "engine_supplier"
    SPONSOR = "sponsor"


class ChiefRole:
    OWNER = "owner"
    CTO   = "cto"
    CMO   = "cmo"
    CPO   = "cpo"


class Chief:
    def __init__(
        self,
        role: str,
        first_name: str,
        last_name: str,
        country: "Country | str | None",
        age: int,
        skill_primary: int,
        skill_secondary: Optional[int] = None,
        contract_years: int = 3,
        db_id: int = 0,
    ):
        self.db_id = db_id
        self.role = role
        self.first_name = first_name
        self.last_name = last_name
        if isinstance(country, str):
            self.country = get_country(country)
        else:
            self.country = country
        self.age = age
        self.skill_primary = skill_primary
        self.skill_secondary = skill_secondary
        self.contract_years = contract_years
        self.retired = False
        self.team: Optional["Team"] = None

    @property
    def nationality(self) -> str | None:
        return self.country.code if self.country else None

    @property
    def name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    def yearly_skill_update(self) -> None:
        if random.random() < 0.33 and self.skill_primary < 100:
            self.skill_primary += 1
        if self.skill_secondary is not None and random.random() < 0.33 and self.skill_secondary < 100:
            self.skill_secondary += 1

    def tick_contract(self) -> None:
        if self.contract_years > 0:
            self.contract_years -= 1

    def is_contract_expired(self) -> bool:
        return self.contract_years == 0

    def should_retire_as_free_agent(self) -> bool:
        if self.age >= TeamConstants.CHIEF_RETIRE_AGE:
            return True
        if self.age >= 60 and random.random() < TeamConstants.CHIEF_RETIRE_PROB_60:
            return True
        return False


class ChiefGenerator:
    def __init__(self, names_dir: str = "./names"):
        self.names_dir = names_dir
        self._driver_gen = DriverGenerator(names_dir=names_dir)
        self._name_structure = self._driver_gen.name_structure

    def _pick_name(self, nat_code: str) -> Tuple[str, str]:
        names = self._name_structure.get(nat_code)
        if not names:
            # Fallback to a random nationality
            nat_code = random.choice(list(self._name_structure.keys()))
            names = self._name_structure[nat_code]
        first = random.choice(names["first"])
        last = random.choice(names["last"])
        return first, last

    def generate_owner(
        self, team_name: str, team_nationality: Optional[str], owner_type: str
    ) -> Chief:
        nat_code = team_nationality if team_nationality in self._name_structure else (
            random.choice(list(self._name_structure.keys()))
        )
        first, last = self._pick_name(nat_code)
        if owner_type == OwnerType.INDIVIDUAL:
            last = team_name
        age = random.randint(TeamConstants.CHIEF_GEN_MIN_AGE_OWNER, TeamConstants.CHIEF_GEN_MAX_AGE_OWNER)
        skill = random.randint(TeamConstants.CHIEF_GEN_SKILL_MIN, TeamConstants.CHIEF_GEN_SKILL_MAX)
        return Chief(
            role=ChiefRole.OWNER,
            first_name=first,
            last_name=last,
            country=get_country(nat_code),
            age=age,
            skill_primary=skill,
            contract_years=-1,
        )

    def generate_owner_successor(self, predecessor: Chief) -> Chief:
        nat_code = predecessor.nationality
        if not nat_code or nat_code not in self._name_structure:
            nat_code = random.choice(list(self._name_structure.keys()))
        first, _ = self._pick_name(nat_code)
        age = random.randint(
            TeamConstants.CHIEF_GEN_MIN_AGE_OWNER,
            TeamConstants.CHIEF_GEN_MAX_AGE_OWNER,
        )
        skill = random.randint(TeamConstants.CHIEF_GEN_SKILL_MIN, TeamConstants.CHIEF_SUCCESSOR_SKILL_MAX)
        return Chief(
            role=ChiefRole.OWNER,
            first_name=first,
            last_name=predecessor.last_name,
            country=predecessor.country,
            age=age,
            skill_primary=skill,
            contract_years=-1,
        )

    def _generate_staff_chief(self, role: str, nat_code: Optional[str], secondary: bool = False) -> Chief:
        if not nat_code or nat_code not in self._name_structure:
            nat_code = random.choice(list(self._name_structure.keys()))
        first, last = self._pick_name(nat_code)
        age = random.randint(TeamConstants.CHIEF_GEN_MIN_AGE, TeamConstants.CHIEF_GEN_MAX_AGE)
        skill_p = random.randint(TeamConstants.CHIEF_GEN_SKILL_MIN, TeamConstants.CHIEF_GEN_SKILL_MAX)
        skill_s = random.randint(TeamConstants.CHIEF_GEN_SKILL_MIN, TeamConstants.CHIEF_GEN_SKILL_MAX) if secondary else None
        contract = random.randint(TeamConstants.CHIEF_CONTRACT_MIN, TeamConstants.CHIEF_CONTRACT_MAX)
        return Chief(
            role=role,
            first_name=first,
            last_name=last,
            country=get_country(nat_code),
            age=age,
            skill_primary=skill_p,
            skill_secondary=skill_s,
            contract_years=contract,
        )

    def generate_cto(self, nat_code: Optional[str] = None) -> Chief:
        return self._generate_staff_chief(ChiefRole.CTO, nat_code, secondary=True)

    def generate_cmo(self, nat_code: Optional[str] = None) -> Chief:
        return self._generate_staff_chief(ChiefRole.CMO, nat_code, secondary=False)

    def generate_cpo(self, nat_code: Optional[str] = None) -> Chief:
        return self._generate_staff_chief(ChiefRole.CPO, nat_code, secondary=False)


class Engine:
    def __init__(
        self,
        name: str,
        power: float,
        reliability: float,
        color_primary: str,
        color_secondary: str,
        db_id: int = 0,
        nationality: Country | str | None = None,
    ):
        self.db_id = db_id
        self.name = name
        self.power = power
        self.reliability = reliability
        self.color_primary = color_primary
        self.color_secondary = color_secondary
        h = color_primary.lstrip("#")
        self.rgb_primary: tuple[int, int, int] = (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
        
        # Handle both Country objects and string codes
        if isinstance(nationality, str):
            self.country = get_country(nationality)
        else:
            self.country = nationality
        
        self.teams: List[Team] = []
        self.value: float = 0.0
        self.update_value()
    
    @property
    def nationality(self) -> str | None:
        """Return nationality code for database storage."""
        return self.country.code if self.country else None

    def update_value(self):
        self.value = (self.power + self.reliability) / 2

    def add_team(self, team: Team):
        self.teams.append(team)

    def remove_team(self, r_team: Team):
        try:
            self.teams.remove(r_team)
        except ValueError:
            pass


class Team:
    def __init__(
        self,
        name: str,
        drivers: List[Optional[Driver]],
        driver_contracts: List[int],
        chassis: float,
        engine: Engine,
        color_primary: str,
        color_secondary: str,
        engine_contract: int = 3,
        sponsor: Optional[Sponsor] = None,
        sponsor_contract: int = 0,
        db_id: int = 0,
        nationality: Country | str | None = None,
        owner_type: str = OwnerType.INDIVIDUAL,
        owner_engine: Optional[Engine] = None,
        owner_sponsor: Optional[Sponsor] = None,
        finance_base: int = 2,
    ):
        if len(drivers) != TeamConstants.MAX_DRIVERS:
            raise ValueError(f"Team must have exactly {TeamConstants.MAX_DRIVERS} drivers")
        self.db_id = db_id
        self.name = name
        self.drivers = drivers
        self.driver_contracts = driver_contracts
        self.chassis = chassis
        self.engine = engine
        self.color_primary = color_primary
        self.color_secondary = color_secondary
        h = color_primary.lstrip("#")
        self.rgb_primary: tuple[int, int, int] = (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
        self.engine_contract = engine_contract
        self.sponsor: Optional[Sponsor] = sponsor
        self.sponsor_contract: int = sponsor_contract

        # Chief slots
        self.owner: Optional[Chief] = None
        self.cto: Optional[Chief] = None
        self.cmo: Optional[Chief] = None
        self.cpo: Optional[Chief] = None

        # Handle both Country objects and string codes
        if isinstance(nationality, str):
            self.country = get_country(nationality)
        else:
            self.country = nationality
        
        self.owner_type = owner_type
        self.owner_engine: Optional[Engine] = owner_engine
        self.owner_sponsor: Optional[Sponsor] = owner_sponsor
        self.finance_base: int = finance_base
        self.position_history: List[int] = []
    
    @property
    def nationality(self) -> str | None:
        """Return nationality code for database storage."""
        return self.country.code if self.country else None

    @property
    def cto_development(self) -> float:
        if self.cto is not None:
            return self.cto.skill_primary / 100.0
        return TeamConstants.CHIEF_VACANCY_FLOOR

    @property
    def cto_eng_scouting(self) -> float:
        if self.cto is not None and self.cto.skill_secondary is not None:
            return self.cto.skill_secondary / 100.0
        return TeamConstants.CHIEF_VACANCY_FLOOR

    @property
    def cpo_scouting(self) -> float:
        if self.cpo is not None:
            return self.cpo.skill_primary / 100.0
        return TeamConstants.CHIEF_VACANCY_FLOOR

    @property
    def owner_scouting_factor(self) -> float:
        if self.owner is not None:
            return 0.4 + (self.owner.skill_primary / 100.0) * 0.6
        return 0.4

    @property
    def avg_skill_100(self) -> int:
        if self.engine is None:
            return 0
        return int(((self.chassis + self.engine.value) / 2) * 100)

    def remove_engine(self):
        if self.engine is None:
            return
        engine = self.engine
        self.engine = None
        self.engine_contract = -1
        engine.remove_team(self)

    def tick_engine_contract(self) -> None:
        self.engine_contract -= 1

    def is_engine_contract_still_valid(self) -> bool:
        # Only treat as expired when it hits exactly 0; negative means already removed
        return self.engine_contract != 0

    def remove_sponsor(self):
        if self.sponsor is None:
            return
        sponsor = self.sponsor
        self.sponsor = None
        self.sponsor_contract = -1
        sponsor.release_team()

    def tick_sponsor_contract(self) -> None:
        self.sponsor_contract -= 1

    def is_sponsor_contract_still_valid(self) -> bool:
        return self.sponsor_contract != 0

    def tick_driver_contracts(self) -> None:
        for i in range(2):
            self.driver_contracts[i] -= 1

    def are_driver_contracts_valid(self) -> Tuple[bool, bool]:
        return self.driver_contracts[0] != 0, self.driver_contracts[1] != 0
