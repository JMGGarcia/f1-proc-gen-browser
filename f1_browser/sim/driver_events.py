"""Driver event system: loading, eligibility, rolling, and ticking."""
from __future__ import annotations

import json
import os
import random
from typing import TYPE_CHECKING, List, Optional, Tuple

from db import models as m
from sim.drivers import ModifierSnapshot

if TYPE_CHECKING:
    from sim.drivers import Driver

_EVENT_DEFS: Optional[List[dict]] = None


def _load_event_defs() -> List[dict]:
    global _EVENT_DEFS
    if _EVENT_DEFS is None:
        folder = os.path.join(os.path.dirname(__file__), "..", "data", "driver_events")
        all_defs: List[dict] = []
        for fname in sorted(os.listdir(folder)):
            if fname.endswith(".json"):
                with open(os.path.join(folder, fname), "r", encoding="utf-8") as f:
                    all_defs.extend(json.load(f))
        _EVENT_DEFS = all_defs
    return _EVENT_DEFS


def get_event_def_by_id(event_def_id: str) -> Optional[dict]:
    """Return the event definition dict for the given id, or None if not found."""
    return next((d for d in _load_event_defs() if d["id"] == event_def_id), None)


def get_form_event_defs() -> Tuple[dict, dict]:
    """Return (form_low_def, form_high_def) for programmatic application."""
    defs = _load_event_defs()
    low = next(d for d in defs if d["id"] == "form_low")
    high = next(d for d in defs if d["id"] == "form_high")
    return low, high


def get_eligible_events(driver: "Driver", active_event_ids: List[str]) -> List[dict]:
    """Return rollable event defs (weight > 0, not form) that pass all conditions."""
    defs = _load_event_defs()
    eligible = []
    for ev in defs:
        if ev.get("weight", 0) == 0:
            continue
        if ev.get("is_form", False):
            continue
        cond = ev.get("conditions", {})
        if not _check_conditions(driver, active_event_ids, cond):
            continue
        eligible.append(ev)
    return eligible


def roll_event(eligible: List[dict]) -> Optional[dict]:
    """Weighted random pick from eligible events. Returns None if list is empty."""
    if not eligible:
        return None
    weights = [ev.get("weight", 1) for ev in eligible]
    return random.choices(eligible, weights=weights, k=1)[0]


def _check_conditions(driver: "Driver", active_event_ids: List[str], cond: dict) -> bool:
    if not cond:
        return True
    if "min_skill" in cond and driver.skill < cond["min_skill"]:
        return False
    if "max_skill" in cond and driver.skill > cond["max_skill"]:
        return False
    if "min_age" in cond and driver.age < cond["min_age"]:
        return False
    if "max_age" in cond and driver.age > cond["max_age"]:
        return False
    if "has_team" in cond:
        has_team = driver.team is not None
        if cond["has_team"] != has_team:
            return False
    if "excludes_events" in cond:
        for excl_id in cond["excludes_events"]:
            if excl_id in active_event_ids:
                return False
    return True


def tick_race_modifiers(
    drivers: List["Driver"],
    db,
    season_num: int,
) -> List["tuple[Driver, ModifierSnapshot]"]:
    """Decrement remaining for race-based active modifiers. Return expired (driver, snapshot) pairs."""
    expired = []
    driver_map = {d.db_id: d for d in drivers}

    race_mods = (
        db.query(m.DriverModifier)
        .filter_by(active=True, duration_type="races")
        .all()
    )
    for mod in race_mods:
        if mod.remaining is None:
            continue
        mod.remaining -= 1
        if mod.remaining <= 0:
            mod.active = False
            driver = driver_map.get(mod.driver_id)
            if driver:
                snapshot = next((s for s in driver.active_modifiers if s.db_id == mod.id), None)
                if snapshot:
                    snapshot.remaining = mod.remaining
                    snapshot.active = False
                    expired.append((driver, snapshot))
    return expired


def batch_tick_race_modifiers(
    drivers: List["Driver"],
    db,
    race_count: int,
    season_num: int,
) -> List["tuple[Driver, ModifierSnapshot]"]:
    """Batch-decrement race-based modifiers by race_count. Return expired (driver, snapshot) pairs."""
    expired = []
    driver_map = {d.db_id: d for d in drivers}

    race_mods = (
        db.query(m.DriverModifier)
        .filter_by(active=True, duration_type="races")
        .all()
    )
    for mod in race_mods:
        if mod.remaining is None:
            continue
        mod.remaining -= race_count
        if mod.remaining <= 0:
            mod.active = False
            driver = driver_map.get(mod.driver_id)
            if driver:
                snapshot = next((s for s in driver.active_modifiers if s.db_id == mod.id), None)
                if snapshot:
                    snapshot.remaining = mod.remaining
                    snapshot.active = False
                    expired.append((driver, snapshot))
    return expired
