from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator

import pytest
from fakeredis import FakeAsyncRedis, FakeServer
from moto import mock_aws

from lumira_recognizer.config import RecognizerSettings
from lumira_shared import S3Storage, ServiceContext, StreamPublisher, get_logger
from lumira_shared.models import ParsedPlan, Point2D, Segment, SourceFormat, TextItem

# Gleiche Wohnung wie lumira_parser.samples – hier dupliziert, damit der recognizer
# ohne den parser testbar bleibt (unabhängige Services).
WALLS = [
    (0, 0, 10_000, 0),
    (10_000, 0, 10_000, 8_000),
    (10_000, 8_000, 0, 8_000),
    (0, 8_000, 0, 0),
    (6_000, 0, 6_000, 8_000),
    (6_000, 4_500, 10_000, 4_500),
    (0, 2_000, 6_000, 2_000),
]
LABELS = [
    ("Wohnen/Essen 36,00 m²", 3_000, 5_000),
    ("Schlafen 14,00 m²", 8_000, 6_300),
    ("Bad 18,00 m²", 8_000, 2_200),
    ("Flur 12,00 m²", 3_000, 1_000),
]


@pytest.fixture
def parsed_plan() -> ParsedPlan:
    return ParsedPlan(
        project_id=uuid.uuid4(),
        source_key="projects/x/upload/floor_plan.dxf",
        source_format=SourceFormat.DXF,
        width_mm=10_000,
        height_mm=8_000,
        segments=[
            Segment(start=Point2D(x=x1, y=y1), end=Point2D(x=x2, y=y2)) for x1, y1, x2, y2 in WALLS
        ],
        texts=[TextItem(text=t, position=Point2D(x=x, y=y)) for t, x, y in LABELS],
    )


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch) -> Iterator[S3Storage]:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-central-1")
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    with mock_aws():
        yield S3Storage("lumira-test")


@pytest.fixture
async def ctx(storage: S3Storage) -> AsyncIterator[ServiceContext]:
    await storage.ensure_bucket()
    redis = FakeAsyncRedis(server=FakeServer(), decode_responses=True)
    yield ServiceContext(
        settings=RecognizerSettings(_env_file=None, recognizer_device="cpu"),  # pyright: ignore[reportCallIssue]
        redis=redis,
        publisher=StreamPublisher(redis),
        storage=storage,
        log=get_logger("test"),
    )
    await redis.aclose()
