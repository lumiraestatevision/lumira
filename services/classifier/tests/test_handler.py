from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fakeredis import FakeAsyncRedis, FakeServer
from moto import mock_aws

from lumira_classifier.config import ClassifierSettings
from lumira_classifier.handler import classify, handle_plan_recognized
from lumira_classifier.logic.geometry import classify_geometry
from lumira_shared import (
    Artifact,
    EventType,
    S3Storage,
    ServiceContext,
    StreamPublisher,
    get_logger,
    project_created,
)
from lumira_shared.models import FloorPlan, Point2D, Room, RoomType, SourceFormat


def _rect(w: float, h: float, label: str | None = None) -> Room:
    return Room(
        polygon=[Point2D(x=0, y=0), Point2D(x=w, y=0), Point2D(x=w, y=h), Point2D(x=0, y=h)],
        label=label,
    )


def _plan(*rooms: Room) -> FloorPlan:
    return FloorPlan(
        project_id=uuid.uuid4(),
        source_key="plan.dxf",
        source_format=SourceFormat.DXF,
        rooms=list(rooms),
    )


def test_label_wins_over_geometry() -> None:
    plan = classify(_plan(_rect(6_000, 6_000, "Bad 36,00 m²")))  # riesig, aber beschriftet
    [room] = plan.rooms
    assert room.room_type is RoomType.BATHROOM
    assert room.confidence == 0.9
    assert room.label_area_m2 == 36.0


@pytest.mark.parametrize(
    ("w", "h", "expected"),
    [
        (1_000, 2_000, RoomType.WC),  # 2 m², schmal
        (1_300, 1_500, RoomType.STORAGE),  # 1,95 m², fast quadratisch
        (6_000, 5_500, RoomType.LIVING),  # 33 m²
        (1_500, 6_000, RoomType.HALLWAY),  # schmal und lang
        (4_000, 3_800, RoomType.BEDROOM),  # 15,2 m²
    ],
)
def test_geometry_fallback(w: float, h: float, expected: RoomType) -> None:
    assert classify_geometry(_rect(w, h)) is expected


def test_unlabelled_room_gets_low_confidence() -> None:
    [room] = classify(_plan(_rect(4_000, 3_800, "Raum 3"))).rooms
    assert room.confidence == 0.3


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch) -> Iterator[S3Storage]:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-central-1")
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    with mock_aws():
        yield S3Storage("lumira-test")


async def test_handler(storage: S3Storage) -> None:
    await storage.ensure_bucket()
    redis = FakeAsyncRedis(server=FakeServer(), decode_responses=True)
    ctx = ServiceContext(
        settings=ClassifierSettings(_env_file=None),  # pyright: ignore[reportCallIssue]
        redis=redis,
        publisher=StreamPublisher(redis),
        storage=storage,
        log=get_logger("test"),
    )
    plan = _plan(_rect(4_000, 4_000, "Schlafen"), _rect(2_000, 2_500, "Bad"))
    key = f"projects/{plan.project_id}/recognizer/floor_plan.json"
    await storage.put_json(key, plan)
    event = project_created(plan.project_id, floor_plan_key="plan.dxf")
    event = event.follow_up(
        EventType.PLAN_RECOGNIZED, producer="recognizer", artifacts={Artifact.RECOGNIZED_PLAN: key}
    )

    result = await handle_plan_recognized(event, ctx)

    assert result.type is EventType.ROOMS_CLASSIFIED
    assert result.data == {"room_types": {"bedroom": 1, "bathroom": 1}}
    stored = await storage.get_model(result.artifacts[Artifact.CLASSIFIED_PLAN], FloorPlan)
    assert [r.room_type for r in stored.rooms] == [RoomType.BEDROOM, RoomType.BATHROOM]
    await redis.aclose()
