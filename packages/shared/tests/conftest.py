from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from uuid import UUID, uuid4

import pytest
from fakeredis import FakeAsyncRedis, FakeServer
from moto import mock_aws

from lumira_shared.models import FloorPlan, Opening, OpeningType, Point2D, Room, SourceFormat, Wall
from lumira_shared.storage import S3Storage


@pytest.fixture
def project_id() -> UUID:
    return uuid4()


@pytest.fixture
async def redis() -> AsyncIterator[FakeAsyncRedis]:
    # Eigener FakeServer je Test → keine geteilten Daten zwischen Tests.
    client = FakeAsyncRedis(server=FakeServer(), decode_responses=True)
    yield client
    await client.aclose()


@pytest.fixture
def aws_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-central-1")
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    monkeypatch.delenv("AWS_ENDPOINT_URL_S3", raising=False)


@pytest.fixture
def storage(aws_env: None) -> Iterator[S3Storage]:
    with mock_aws():
        yield S3Storage("lumira-test")


@pytest.fixture
def floor_plan(project_id: UUID) -> FloorPlan:
    """Rechteckiger Raum 4 m x 3 m mit einer Tür."""
    corners = [
        Point2D(x=0, y=0),
        Point2D(x=4000, y=0),
        Point2D(x=4000, y=3000),
        Point2D(x=0, y=3000),
    ]
    walls = [
        Wall(id=f"wall_{i}", start=a, end=b, thickness_mm=240)
        for i, (a, b) in enumerate(zip(corners, [*corners[1:], corners[0]], strict=True))
    ]
    return FloorPlan(
        project_id=project_id,
        source_key=f"projects/{project_id}/upload/plan.pdf",
        source_format=SourceFormat.PDF,
        walls=walls,
        openings=[
            Opening(
                id="door_1",
                type=OpeningType.DOOR,
                wall_id="wall_0",
                offset_mm=500,
                width_mm=885,
                height_mm=2010,
            )
        ],
        rooms=[Room(id="room_1", polygon=corners, label="Wohnen", wall_ids=[w.id for w in walls])],
    )
