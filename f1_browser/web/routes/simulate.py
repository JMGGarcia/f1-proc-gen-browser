from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from db.models import Season
from db.session import get_db_session
from web import sim_state

router = APIRouter()


@router.post("/simulate")
def simulate(
    db: Session = Depends(get_db_session),
    count: int = Form(default=1),
):
    if not sim_state.is_available():
        return RedirectResponse("/?msg=sim_unavailable", status_code=303)
    if sim_state.is_busy():
        return RedirectResponse("/?msg=sim_busy", status_code=303)

    count = max(1, min(count, 100))  # clamp to 1–100
    latest = db.query(Season).order_by(Season.number.desc()).first()
    next_num = (latest.number + 1) if latest else 1

    sim_state.simulate_many(next_num, count)

    return RedirectResponse("/", status_code=303)
