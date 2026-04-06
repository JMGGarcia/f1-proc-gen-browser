from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from db.models import WorldEvent
from db.session import get_db_session
from web.templates_env import templates

router = APIRouter(prefix="/events")

PAGE_SIZE = 60


@router.get("/")
def events_list(request: Request, page: int = 1, db: Session = Depends(get_db_session)):
    offset = (page - 1) * PAGE_SIZE
    total = db.query(WorldEvent).count()
    events = (
        db.query(WorldEvent)
        .order_by(WorldEvent.id.desc())
        .offset(offset)
        .limit(PAGE_SIZE)
        .all()
    )
    return templates.TemplateResponse(request, "events.html", {
        "events": events,
        "page": page,
        "total_pages": max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE),
    })
