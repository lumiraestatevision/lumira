"""Lumira backend – API Gateway (Port 8000).

Start lokal:  uv run --package lumira-backend uvicorn lumira_backend.main:app --reload
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis

from lumira_backend.api import projects
from lumira_backend.config import BackendSettings
from lumira_backend.db import Database
from lumira_backend.orchestrator import build_handlers
from lumira_shared import S3Storage, ServiceContext, create_service_app


def create_app(
    settings: BackendSettings,
    *,
    db: Database | None = None,
    redis: Redis | None = None,
    storage: S3Storage | None = None,
) -> FastAPI:
    database = db or Database(settings.database_url)

    @asynccontextmanager
    async def lifespan_hook(app: FastAPI, ctx: ServiceContext) -> AsyncIterator[None]:
        app.state.db = database
        try:
            yield
        finally:
            await database.dispose()

    app = create_service_app(
        settings,
        handlers=build_handlers(database, settings),
        lifespan_hook=lifespan_hook,
        readiness_checks={"database": database.ping},
        redis=redis,
        storage=storage,
        description="Projekte anlegen, Grundriss und Leistungsverzeichnis hochladen, Status verfolgen.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(projects.router)
    return app


app = create_app(BackendSettings())
