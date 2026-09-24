from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fakeredis import FakeAsyncRedis, FakeServer
from fastapi.testclient import TestClient
from moto import mock_aws

from lumira_shared import (
    Artifact,
    EventType,
    S3Storage,
    ServiceContext,
    StreamPublisher,
    get_logger,
    project_created,
)
from lumira_unreal.config import UnrealSettings
from lumira_unreal.handler import handle_model_generated


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch) -> Iterator[S3Storage]:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-central-1")
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    with mock_aws():
        yield S3Storage("lumira-test")


async def test_stub_writes_manifest(storage: S3Storage) -> None:
    await storage.ensure_bucket()
    redis = FakeAsyncRedis(server=FakeServer(), decode_responses=True)
    ctx = ServiceContext(
        settings=UnrealSettings(_env_file=None),  # pyright: ignore[reportCallIssue]
        redis=redis,
        publisher=StreamPublisher(redis),
        storage=storage,
        log=get_logger("t"),
    )
    model = project_created(uuid.uuid4(), floor_plan_key="plan.dxf").follow_up(
        EventType.MODEL_GENERATED,
        producer="generator",
        artifacts={Artifact.MODEL_FBX: "m.fbx", Artifact.MODEL_GLTF: "m.glb"},
    )

    event = await handle_model_generated(model, ctx)

    assert event.type is EventType.VR_EXPORTED
    manifest = await storage.get_json(event.artifacts[Artifact.VR_PACKAGE])
    assert manifest["status"] == "stub"
    assert manifest["source"] == {"fbx": "m.fbx", "gltf": "m.glb"}
    await redis.aclose()


def test_health() -> None:
    from lumira_unreal.main import app

    assert TestClient(app).get("/health").json()["service"] == "unreal"
