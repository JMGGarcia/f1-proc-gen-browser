from fastapi import FastAPI

from db.models import Driver, DriverSeasonStats, Season
from db.session import get_session
from web.routes import index, seasons, drivers, teams, engines, stats, simulate
from web.templates_env import templates

app = FastAPI(title="F1 World")

app.include_router(index.router)
app.include_router(seasons.router)
app.include_router(drivers.router)
app.include_router(teams.router)
app.include_router(engines.router)
app.include_router(stats.router)
app.include_router(simulate.router)


def _get_nav_context() -> dict:
    """Return data shared across all nav renders."""
    try:
        with get_session() as db:
            latest = (
                db.query(Season)
                .filter_by(completed=True)
                .order_by(Season.number.desc())
                .first()
            )
            if not latest:
                return {"nav_season": None, "nav_champion": None}
            champ_stats = (
                db.query(DriverSeasonStats)
                .filter_by(season_id=latest.id, championship_position=1)
                .first()
            )
            champ = (
                db.query(Driver).filter_by(id=champ_stats.driver_id).first()
                if champ_stats else None
            )
            # Extract into a plain dict so the session can close safely
            champ_data = {
                "id": champ.id,
                "first_name": champ.first_name,
                "last_name": champ.last_name,
            } if champ else None
            return {
                "nav_season": latest.number,
                "nav_champion": champ_data,
            }
    except Exception:
        return {"nav_season": None, "nav_champion": None}


# Register nav_ctx on the shared templates instance so all routes get it
templates.env.globals["nav_ctx"] = _get_nav_context
