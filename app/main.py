"""Application factory.

Run with:  uvicorn app.main:create_app --factory --reload --reload-dir app

`create_app` builds a fresh application from a Settings object. Tests call it
with a temporary database and storage folder, so they never touch real data.
"""
import logging
import threading
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI

from app.config import Settings
from app.database import Base, make_engine, make_session_factory
from app.routes import router
from app.worker import find_unfinished_job_ids, process_job

logger = logging.getLogger(__name__)


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    settings = settings or Settings.from_env()
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)  # create tables if they do not exist yet
    session_factory = make_session_factory(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup: background work lives in this process, so a restart
        # interrupts it. All job state is in the database, so pick up any job
        # that was left unfinished; only its still-pending certificates remain.
        for job_id in find_unfinished_job_ids(session_factory):
            logger.info("Resuming unfinished job %s", job_id)
            threading.Thread(
                target=process_job,
                args=(job_id, session_factory, settings.storage_dir),
                daemon=True,
            ).start()
        yield
        engine.dispose()

    app = FastAPI(
        title="Bulk Certificate Generator",
        version="1.0.0",
        description="Submit a list of recipients, track the job, download the certificates.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.session_factory = session_factory
    app.include_router(router)

    @app.get("/health", tags=["health"])
    def health() -> dict:
        return {"status": "ok"}

    return app