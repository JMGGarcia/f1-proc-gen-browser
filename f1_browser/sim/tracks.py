from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Track:
    name: str
    downforce_over_engine: float  # chassis importance vs engine
    car_over_driver: float        # car quality importance vs driver skill
    target_lap_time: float = 90.0  # ideal lap time in seconds (best car + driver, no noise)
    db_id: int = field(default=0, compare=False)
