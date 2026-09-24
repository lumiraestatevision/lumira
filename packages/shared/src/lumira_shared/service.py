"""Gemeinsames Service-Grundgerüst.

``create_service_app`` liefert jedem Service dieselbe Basis:
- FastAPI-App mit ``/health`` (Liveness) und ``/health/ready`` (Readiness)
- strukturiertes Logging
- Redis-, S3- und Publisher-Verbindungen im ``ServiceContext``
- Event-Consumer als Hintergrund-Task im Lifespan, sauberes Herunterfahren

Ein Worker-Service besteht damit im Kern nur aus seinen Event-Handlern:

    async def handle(event: Event, ctx: ServiceContext) -> Event:
        ...
        return event.follow_up(EventType.PLAN_PARSED, producer=ctx.settings.service_name, ...)

    app = create_service_app(ParserSettings(), handlers={EventType.PROJECT_CREATED: handle})
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal

import structlog
from fastapi import APIRouter, FastAPI, Request, Response
from pydantic import BaseModel
from redis.asyncio import Redis

from lumira_shared.events import Event, EventType
from lumira_shared.log import configure_logging, get_logger
from lumira_shared.settings import BaseServiceSettings
from lumira_shared.storage import S3Storage
from lumira_shared.streams import EventHandler, HandlerResult, StreamConsumer, StreamPublisher

log = get_logger(__name__)


@dataclass(slots=True)
class ServiceContext:
    """Alles, was ein Handler braucht – in Tests einfach mit Fakes befüllbar."""

    settings: BaseServiceSettings
    redis: Redis
    publisher: StreamPublisher
    storage: S3Storage
    log: structlog.stdlib.BoundLogger


ContextHandler = Callable[[Event, ServiceContext], Awaitable[HandlerResult]]
LifespanHook = Callable[[FastAPI, ServiceContext], AbstractAsyncContextManager[None]]


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str
    version: str


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, bool]


def create_service_app(
    settings: BaseServiceSettings,
    *,
    handlers: Mapping[EventType, ContextHandler] | None = None,
    lifespan_hook: LifespanHook | None = None,
    redis: Redis | None = None,
    storage: S3Storage | None = None,
    description: str = "",
) -> FastAPI:
    """Baut die FastAPI-App eines Services.

    ``redis`` und ``storage`` lassen sich für Tests injizieren; sonst entstehen sie aus den
    Settings. ``lifespan_hook`` erlaubt zusätzliche Ressourcen (z. B. die DB im backend).
    """
    configure_logging(settings.service_name, level=settings.log_level, json_logs=settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        redis_client = redis or Redis.from_url(settings.redis_url, decode_responses=True)
        publisher = StreamPublisher(redis_client)
        ctx = ServiceContext(
            settings=settings,
            redis=redis_client,
            publisher=publisher,
            storage=storage or S3Storage(settings.s3_bucket),
            log=get_logger(settings.service_name),
        )
        app.state.ctx = ctx

        stop = asyncio.Event()
        consumer_task: asyncio.Task[None] | None = None
        if handlers:
            consumer = StreamConsumer(
                redis_client,
                group=settings.service_name,
                consumer=settings.consumer_name,
                handlers={t: _bind(h, ctx) for t, h in handlers.items()},
                publisher=publisher,
                options=settings.consumer_options(),
            )
            consumer_task = asyncio.create_task(
                consumer.run(stop), name=f"{settings.service_name}-consumer"
            )
        app.state.consumer_task = consumer_task

        log.info(
            "service.started",
            port=settings.port,
            environment=settings.environment,
            consumes=[str(t) for t in handlers or {}],
        )
        try:
            if lifespan_hook is None:
                yield
            else:
                async with lifespan_hook(app, ctx):
                    yield
        finally:
            stop.set()
            if consumer_task is not None:
                try:
                    await asyncio.wait_for(consumer_task, timeout=10)
                except TimeoutError:
                    consumer_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await consumer_task
            if redis is None:
                await redis_client.aclose()
            log.info("service.stopped")

    app = FastAPI(
        title=f"lumira-{settings.service_name}",
        version=settings.service_version,
        description=description,
        lifespan=lifespan,
    )
    app.include_router(_health_router(settings))
    return app


def get_context(request: Request) -> ServiceContext:
    """FastAPI-Dependency: ``ctx: Annotated[ServiceContext, Depends(get_context)]``."""
    return request.app.state.ctx


def _bind(handler: ContextHandler, ctx: ServiceContext) -> EventHandler:
    async def bound(event: Event) -> HandlerResult:
        return await handler(event, ctx)

    return bound


def _health_router(settings: BaseServiceSettings) -> APIRouter:
    router = APIRouter(tags=["health"])

    @router.get("/health")
    async def health() -> HealthResponse:
        """Liveness: Der Prozess lebt und beantwortet Requests."""
        return HealthResponse(service=settings.service_name, version=settings.service_version)

    @router.get("/health/ready")
    async def ready(request: Request, response: Response) -> ReadinessResponse:
        """Readiness: Redis erreichbar und – falls vorhanden – Consumer aktiv."""
        ctx: ServiceContext = request.app.state.ctx
        checks: dict[str, bool] = {"redis": await _redis_ok(ctx.redis)}
        task: asyncio.Task[None] | None = request.app.state.consumer_task
        if task is not None:
            checks["consumer"] = not task.done()
        ok = all(checks.values())
        if not ok:
            response.status_code = 503
        return ReadinessResponse(status="ready" if ok else "not_ready", checks=checks)

    return router


async def _redis_ok(redis: Redis) -> bool:
    try:
        result: Any = await redis.ping()
    except Exception:
        return False
    return bool(result)
