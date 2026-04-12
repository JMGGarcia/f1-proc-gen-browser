"""Helper to compute effective driver stats from active DB modifiers."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def compute_modifier_sums(db: Session, driver_ids: list[int]) -> dict[int, dict[str, float]]:
    """Batch-query active DriverModifiers; return {driver_id: {attr: sum}}."""
    if not driver_ids:
        return {}

    from db.models import DriverModifier

    rows = (
        db.query(DriverModifier)
        .filter(
            DriverModifier.driver_id.in_(driver_ids),
            DriverModifier.active == True,
        )
        .all()
    )

    result: dict[int, dict[str, float]] = {}
    for row in rows:
        try:
            mods = json.loads(row.modifier_json) if isinstance(row.modifier_json, str) else row.modifier_json
        except Exception:
            continue
        sums = result.setdefault(row.driver_id, {})
        for attr, val in mods.items():
            sums[attr] = sums.get(attr, 0.0) + val

    return result
