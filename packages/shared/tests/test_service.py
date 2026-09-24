from __future__ import annotations

import time
from uuid import uuid4

from fakeredis import FakeAsyncRedis, FakeServer
from fastapi.testclient import TestClient

from lumira_shared.events import Artifact, Event, EventType, project_created, stream_name
from lumira_shared.service import ServiceContext, create_service_app
from lumira_shared.settings import BaseServiceSettings
from lumira_shared.storage import S3Storage


class DemoSettings(BaseServiceSettings):
    service_name: str = "demo"
    log_json: bool = False
    # fakeredis setzt blockierendes XREADGROUP als Dauerschleife um, die den Event-Loop
    # belegt. Echtes Redis blockiert serverseitig; im Test daher pollen statt blockieren.
    consumer_block_ms: int | None = None


async def parse(event: Event, ctx: ServiceContext) -> Event:
    return event.follow_up(
        EventType.PLAN_PARSED,
        producer=ctx.settings.service_name,
        artifacts={Artifact.PARSED_PLAN: "parsed.json"},
    )


def _wait_until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_health_and_event_roundtrip(aws_env: None) -> None:
    redis = FakeAsyncRedis(server=FakeServer(), decode_responses=True)
    app = create_service_app(
        DemoSettings(),
        handlers={EventType.PROJECT_CREATED: parse},
        redis=redis,
        storage=S3Storage("unused"),
    )

    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json() == {"status": "ok", "service": "demo", "version": "0.1.0"}

        ready = client.get("/health/ready")
        assert ready.status_code == 200
        assert ready.json()["checks"] == {"redis": True, "consumer": True}

        # Aufrufe in den Event-Loop der App, in dem auch der Consumer läuft.
        portal = client.portal
        assert portal is not None
        ctx: ServiceContext = app.state.ctx
        portal.call(ctx.publisher.publish, project_created(uuid4(), floor_plan_key="plan.pdf"))

        def parsed_published() -> bool:
            return portal.call(redis.xlen, stream_name(EventType.PLAN_PARSED)) == 1

        assert _wait_until(parsed_published)
