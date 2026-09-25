from __future__ import annotations

import math
import uuid
from collections.abc import AsyncIterator, Callable, Iterator

import pytest
from fakeredis import FakeAsyncRedis, FakeServer
from moto import mock_aws

from lumira_recognizer.config import RecognizerSettings
from lumira_shared import S3Storage, ServiceContext, StreamPublisher, get_logger
from lumira_shared.models import (
    FilledArea,
    ParsedPlan,
    Point2D,
    Segment,
    SourceFormat,
    Stroke,
    TextItem,
)

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


# CAD-Beispiel wie lumira_parser.samples.sample_cad_pdf: Wände grau gefüllt, Räume farbig.
# Wohnen 5,00 x 6,00 m, Bad 3,00 x 6,00 m; Fenster 1,50 m unten und 1,00 m rechts,
# Tür 0,90 m in der Innenwand mit Aufschlag ins Wohnen (Drehpunkt 5300/1000).
CAD_WALLS = [
    (0, 0, 1_500, 300),
    (3_000, 0, 8_715, 300),
    (0, 6_300, 8_715, 6_600),
    (0, 300, 300, 6_300),
    (8_415, 300, 8_715, 2_500),
    (8_415, 3_500, 8_715, 6_300),
    (5_300, 300, 5_415, 1_000),
    (5_300, 1_900, 5_415, 6_300),
]
CAD_ROOMS = [
    ("Wohnen", "F: 30,00 m²", (300, 300, 5_300, 6_300), "#FFFFA8"),
    ("Bad", "F: 18,00 m²", (5_415, 300, 8_415, 6_300), "#D6FFA8"),
]
CadPlanFactory = Callable[..., ParsedPlan]


def _rect(x0: float, y0: float, x1: float, y1: float, k: float) -> list[Point2D]:
    return [Point2D(x=x * k, y=y * k) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]


@pytest.fixture
def cad_plan() -> CadPlanFactory:
    """``factor`` ≠ 1 simuliert einen falsch angenommenen Maßstab (alles gestreckt);
    ``fills=False`` einen Plan ohne Raumfarben, ``door_arc=False`` ohne Türbogen."""

    def build(*, factor: float = 1.0, fills: bool = True, door_arc: bool = True) -> ParsedPlan:
        areas = [FilledArea(polygon=_rect(*r, factor), color="#808080") for r in CAD_WALLS]
        if fills:
            areas += [FilledArea(polygon=_rect(*r, factor), color=c) for _, _, r, c in CAD_ROOMS]
        texts = []
        for name, area, (x0, y0, x1, y1), _ in CAD_ROOMS:
            cx, cy = (x0 + x1) / 2 * factor, (y0 + y1) / 2 * factor
            texts += [
                TextItem(text=area, position=Point2D(x=cx, y=cy - 300 * factor), height_mm=280),
                TextItem(text=name, position=Point2D(x=cx, y=cy + 200 * factor), height_mm=350),
                TextItem(text="3,00", position=Point2D(x=cx, y=cy - 900 * factor), height_mm=250),
            ]
        arc = [
            Point2D(x=(5_300 - 900 * math.sin(a)) * factor, y=(1_000 + 900 * math.cos(a)) * factor)
            for a in (i * math.pi / 16 for i in range(9))
        ]
        return ParsedPlan(
            project_id=uuid.uuid4(),
            source_key="projects/x/upload/floor_plan.pdf",
            source_format=SourceFormat.PDF,
            width_mm=29_700 * factor,
            height_mm=21_000 * factor,
            plan_scale=100 * factor,
            filled_areas=areas,
            curves=[Stroke(points=arc)] if door_arc else [],
            texts=texts,
        )

    return build


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
