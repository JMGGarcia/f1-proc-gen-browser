from __future__ import annotations

import os
import random
from typing import Dict, List, Optional

from sim.constants import DriverConstants, SimulationConstants
from sim.flags import NATIONALITY_FLAGS


class DriverGenerator:
    def __init__(self, names_dir: str = "./names"):
        self.current_id = 0
        self.names_dir = names_dir
        self.name_structure = self._load_names()

    def generate_driver(self) -> Driver:
        nat = random.choice(list(self.name_structure.keys()))
        first_name = random.choice(self.name_structure[nat]["first"])
        last_name = random.choice(self.name_structure[nat]["last"])
        skill = random.random() * DriverConstants.SKILL_MULTIPLIER
        d = Driver(
            db_id=0,
            first_name=first_name,
            last_name=last_name,
            nationality=nat,
            skill=skill,
            age=random.randint(SimulationConstants.GEN_MIN_AGE, SimulationConstants.GEN_MAX_AGE),
        )
        self.current_id += 1
        return d

    def _load_names(self) -> Dict[str, Dict[str, List[str]]]:
        strut: Dict[str, Dict[str, List[str]]] = {}
        dirs = next(os.walk(self.names_dir))[1]
        for nationality in dirs:
            strut[nationality] = {}
            with open(f"{self.names_dir}/{nationality}/first.txt", "r") as f:
                strut[nationality]["first"] = f.read().splitlines()
            with open(f"{self.names_dir}/{nationality}/last.txt", "r") as f:
                strut[nationality]["last"] = f.read().splitlines()
        return strut


class Driver:
    def __init__(
        self,
        db_id: int,
        first_name: str,
        last_name: str,
        nationality: str,
        skill: float,
        form: str = "M",
        age: int = 20,
    ):
        self.db_id = db_id
        self.first_name = first_name
        self.last_name = last_name
        self.name = f"{first_name} {last_name}"
        self.nationality = nationality
        self.base_skill = skill
        self.skill = skill
        self.top_skill = skill
        self.form = form
        self.age = age
        self.team: Optional[object] = None  # set by Team

    @property
    def flag(self) -> str:
        return NATIONALITY_FLAGS.get(self.nationality, "")

    @property
    def skill_100(self) -> int:
        return int(self.skill * 100)

    @property
    def base_skill_100(self) -> int:
        return int(self.base_skill * 100)

    @property
    def top_skill_100(self) -> int:
        return int(self.top_skill * 100)

    def age_driver(self):
        self.age += 1
        if self.age <= 25:
            self.base_skill += DriverConstants.SKILL_IMPROVEMENT_RATE * random.random()
            if self.base_skill > 1:
                self.base_skill = 1.0
        elif self.age > 30:
            self.base_skill -= DriverConstants.SKILL_IMPROVEMENT_RATE * random.random()
            if self.base_skill < DriverConstants.MIN_SKILL:
                self.base_skill = DriverConstants.MIN_SKILL
        if self.base_skill > self.top_skill:
            self.top_skill = self.base_skill

    def set_skill(self, form: str):
        self.form = form
        change = SimulationConstants.FORM_CHANGE
        if form == "L":
            self.skill = max(0.0, self.base_skill - change)
        elif form == "H":
            self.skill = min(1.0, self.base_skill + change)
        else:
            self.skill = self.base_skill
