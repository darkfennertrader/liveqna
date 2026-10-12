from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .database import Database
from .qdrant import QdrantProbe
from .settings import Settings


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ingestion.main")


async def _dependency_status(check) -> dict[str, Any]:
    try:
        ready = await check()
    except Exception as exc:  # health response must report, not mask, failures
        return {"status": "unavailable", "error": type(exc).__name__}
    return {"status": "ok" if ready else "unavailable"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.from_environment()
    settings.prepare_directories()
    database = Database(settings)
    qdrant = QdrantProbe(
        settings.qdrant_url, settings.dependency_timeout_seconds
    )
    await database.open()
    app.state.settings = settings
    app.state.database = database
    app.state.qdrant = qdrant
    logger.info(
        "ingestion service ready; applied migrations=%s",
        database.applied_migrations,
    )
    try:
        yield
    finally:
        await qdrant.close()
        await database.close()


app = FastAPI(
    title="liveqna-ingestion",
    version="0.1.0-phase0",
    lifespan=lifespan,
)


@app.get("/")
async def service_info() -> dict[str, str]:
    return {
        "service": "liveqna-ingestion",
        "phase": "0",
        "status": "foundation",
    }


@app.get("/health")
async def health(request: Request) -> JSONResponse:
    database, qdrant = await asyncio.gather(
        _dependency_status(request.app.state.database.ping),
        _dependency_status(request.app.state.qdrant.ready),
    )
    healthy = database["status"] == "ok" and qdrant["status"] == "ok"
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={
            "status": "ok" if healthy else "degraded",
            "phase": "0",
            "dependencies": {
                "postgres": database,
                "qdrant": qdrant,
            },
        },
    )
