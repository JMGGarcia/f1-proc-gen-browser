from __future__ import annotations

import math

from sqlalchemy.orm import Session

from db.models import WorldEvent, WorldEventEntity

PAGE_SIZE = 20


def get_entity_events(
    db: Session,
    entity_type: str,
    entity_id: int,
    page: int = 1,
) -> tuple[list, int, int]:
    """Return (events, page, total_pages) for the given entity."""
    base_q = (
        db.query(WorldEvent)
        .join(WorldEventEntity, WorldEventEntity.event_id == WorldEvent.id)
        .filter(
            WorldEventEntity.entity_type == entity_type,
            WorldEventEntity.entity_id == entity_id,
        )
        .order_by(WorldEvent.id.desc())
    )
    total = base_q.count()
    total_pages = max(1, math.ceil(total / PAGE_SIZE))
    page = max(1, min(page, total_pages))
    events = base_q.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all()
    return events, page, total_pages
