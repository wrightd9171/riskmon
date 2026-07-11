from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import scheduler
from .db import init_db
from .web.routes import router


def create_app() -> FastAPI:
    app = FastAPI(title="Risk Monitor")
    static_dir = Path(__file__).parent / "web" / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    app.include_router(router)
    init_db()
    scheduler.start()
    return app


app = create_app()
