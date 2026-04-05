from __future__ import annotations

import random
from typing import List, Optional, Tuple, TYPE_CHECKING

from sim.constants import SimulationConstants, TeamConstants
from sim.countries import get_country

if TYPE_CHECKING:
    from sim.countries import Country

from sim.drivers import Driver
from sim.sponsors import Sponsor


class OwnerType:
    INDIVIDUAL = "individual"
    ENGINE_SUPPLIER = "engine_supplier"
    SPONSOR = "sponsor"


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
        for idx, team in enumerate(self.teams):
            if team.name == r_team.name:
                del self.teams[idx]
                return


class Direction:
    def __init__(self):
        self.years = 0
        self.development = random.random()
        self.scouting = random.random()
        self.eng_scouting = random.random()
        self.avg = (self.development + self.scouting + self.eng_scouting) / 3
        self.position_history: List[int] = []

    @property
    def avg_100(self) -> int:
        return int(self.avg * 100)

    def yearly_update(self, position: int, total_teams: int) -> bool:
        """Returns True if direction changed (team principal fired)."""
        if self.years < 2:
            average_position = 0.0
        else:
            self.position_history.append(position)
            if len(self.position_history) > SimulationConstants.HISTORY_YEARS:
                self.position_history.pop(0)
            average_position = sum(self.position_history) / len(self.position_history)

        if (
            (average_position == total_teams and self.years >= 3)
            or (average_position > (total_teams * 3 / 4) and self.years >= 5 and position > (total_teams * 3 / 4))
            or (average_position > (total_teams / 2) and self.years >= 9 and position > (total_teams / 2))
        ):
            self.years = 0
            self.development = random.random()
            self.scouting = random.random()
            self.eng_scouting = random.random()
            self.avg = (self.development + self.scouting + self.eng_scouting) / 3
            self.position_history = []
            return True

        self.years += 1
        change = SimulationConstants.YEARLY_CHANGE
        for attr in ("development", "scouting", "eng_scouting"):
            val = getattr(self, attr) + random.random() * 2 * change - change
            setattr(self, attr, min(1.0, max(0.0, val)))
        self.avg = (self.development + self.scouting + self.eng_scouting) / 3
        return False

    def get_stats(self) -> Tuple[int, int, int, int]:
        return (
            int(self.avg * 100),
            int(self.development * 100),
            int(self.scouting * 100),
            int(self.eng_scouting * 100),
        )


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
        self.engine_contract = engine_contract
        self.sponsor: Optional[Sponsor] = sponsor
        self.sponsor_contract: int = sponsor_contract
        self.direction = Direction()
        
        # Handle both Country objects and string codes
        if isinstance(nationality, str):
            self.country = get_country(nationality)
        else:
            self.country = nationality
        
        self.owner_type = owner_type
        self.owner_engine: Optional[Engine] = owner_engine
        self.owner_sponsor: Optional[Sponsor] = owner_sponsor
    
    @property
    def nationality(self) -> str | None:
        """Return nationality code for database storage."""
        return self.country.code if self.country else None

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

    def is_engine_contract_still_valid(self) -> bool:
        self.engine_contract -= 1
        # Only treat as expired when it hits exactly 0; negative means already removed
        return self.engine_contract != 0

    def remove_sponsor(self):
        if self.sponsor is None:
            return
        sponsor = self.sponsor
        self.sponsor = None
        self.sponsor_contract = -1
        sponsor.release_team()

    def is_sponsor_contract_still_valid(self) -> bool:
        self.sponsor_contract -= 1
        return self.sponsor_contract != 0

    def is_drivers_contracts_still_valid(self) -> Tuple[bool, bool]:
        results = []
        for i in range(2):
            self.driver_contracts[i] -= 1
            results.append(self.driver_contracts[i] != 0)
        return results[0], results[1]
