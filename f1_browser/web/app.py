from fastapi import FastAPI

from web.routes import index, seasons, drivers, teams, engines, stats, simulate

app = FastAPI(title="F1 World")

app.include_router(index.router)
app.include_router(seasons.router)
app.include_router(drivers.router)
app.include_router(teams.router)
app.include_router(engines.router)
app.include_router(stats.router)
app.include_router(simulate.router)
