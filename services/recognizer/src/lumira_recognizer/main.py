"""Lumira recognizer – KI-Planerkennung (Port 8002). GPU optional, CPU-Fallback."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from lumira_recognizer.config import RecognizerSettings
from lumira_recognizer.handler import handle_plan_parsed
from lumira_recognizer.logic.device import device_info, resolve_device
from lumira_shared import EventType, ServiceContext, create_service_app

settings = RecognizerSettings()


@asynccontextmanager
async def lifespan_hook(app: FastAPI, ctx: ServiceContext) -> AsyncIterator[None]:
    device = await asyncio.to_thread(resolve_device, settings.recognizer_device)
    app.state.device_info = await asyncio.to_thread(device_info, device)
    ctx.log.info("recognizer.device", **app.state.device_info)
    yield


app = create_service_app(
    settings,
    handlers={EventType.PLAN_PARSED: handle_plan_parsed},
    lifespan_hook=lifespan_hook,
    description="Erkennt Wände, Öffnungen und Räume im Grundriss.",
)


@app.get("/info", tags=["health"])
async def info() -> dict[str, str | bool | None]:
    """Welches Rechengerät nutzt der Service? (GPU-Check: curl localhost:8002/info)"""
    return app.state.device_info
