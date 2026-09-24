from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from fakeredis import FakeAsyncRedis
from sqlalchemy import func, select

from lumira_backend.config import BackendSettings
from lumira_backend.db import Database, Project, ProjectEvent, ProjectStatus
from lumira_backend.orchestrator import build_handlers
from lumira_shared import (
    Artifact,
    Event,
    EventType,
    S3Storage,
    ServiceContext,
    StreamPublisher,
    get_logger,
    project_created,
)


@pytest.fixture
def ctx(settings: BackendSettings, redis: FakeAsyncRedis, aws_env: None) -> ServiceContext:
    return ServiceContext(
        settings=settings,
        redis=redis,
        publisher=StreamPublisher(redis),
        storage=S3Storage("unused"),
        log=get_logger("test"),
    )


async def _new_project(db: Database) -> uuid.UUID:
    project_id = uuid.uuid4()
    async with db.sessionmaker() as session, session.begin():
        session.add(Project(id=project_id, name="Musterhaus", artifacts={}))
    return project_id


async def _project(db: Database, project_id: uuid.UUID) -> Project:
    async with db.sessionmaker() as session:
        return await session.get_one(Project, project_id)


def _chain_until_model_generated(project_id: uuid.UUID) -> list[Event]:
    created = project_created(project_id, floor_plan_key="plan.dxf")
    parsed = created.follow_up(
        EventType.PLAN_PARSED, producer="parser", artifacts={Artifact.PARSED_PLAN: "p"}
    )
    recognized = parsed.follow_up(
        EventType.PLAN_RECOGNIZED, producer="recognizer", artifacts={Artifact.RECOGNIZED_PLAN: "r"}
    )
    classified = recognized.follow_up(
        EventType.ROOMS_CLASSIFIED, producer="classifier", artifacts={Artifact.CLASSIFIED_PLAN: "c"}
    )
    blv = classified.follow_up(
        EventType.BLV_PROCESSED, producer="blv", artifacts={Artifact.BLV_RESULT: "b"}
    )
    model = blv.follow_up(
        EventType.MODEL_GENERATED,
        producer="generator",
        artifacts={Artifact.MODEL_FBX: "m.fbx", Artifact.MODEL_GLTF: "m.glb"},
    )
    return [created, parsed, recognized, classified, blv, model]


async def test_chain_without_vr_completes_after_model_generated(
    db: Database, settings: BackendSettings, ctx: ServiceContext
) -> None:
    handlers = build_handlers(db, settings)
    project_id = await _new_project(db)
    *steps, model = _chain_until_model_generated(project_id)

    for event in steps:
        assert await handlers[event.type](event, ctx) is None

    completed = await handlers[EventType.MODEL_GENERATED](model, ctx)
    assert isinstance(completed, Event)
    assert completed.type is EventType.PROJECT_COMPLETED
    assert completed.causation_id == model.event_id

    project = await _project(db, project_id)
    assert project.status is ProjectStatus.PROCESSING
    assert project.current_step == "model.generated"
    assert project.artifacts["model_gltf"] == "m.glb"
    assert project.artifacts["floor_plan_source"] == "plan.dxf"

    assert await handlers[EventType.PROJECT_COMPLETED](completed, ctx) is None
    assert (await _project(db, project_id)).status is ProjectStatus.COMPLETED


async def test_chain_with_vr_waits_for_vr_exported(
    settings_factory: Callable[..., BackendSettings], db: Database, ctx: ServiceContext
) -> None:
    handlers = build_handlers(db, settings_factory(pipeline_vr_enabled=True))
    project_id = await _new_project(db)
    chain = _chain_until_model_generated(project_id)
    for event in chain:
        assert await handlers[event.type](event, ctx) is None

    vr = chain[-1].follow_up(
        EventType.VR_EXPORTED, producer="unreal", artifacts={Artifact.VR_PACKAGE: "vr.json"}
    )
    completed = await handlers[EventType.VR_EXPORTED](vr, ctx)
    assert isinstance(completed, Event)
    assert completed.type is EventType.PROJECT_COMPLETED


async def test_step_failed_marks_project_failed(
    db: Database, settings: BackendSettings, ctx: ServiceContext
) -> None:
    handlers = build_handlers(db, settings)
    project_id = await _new_project(db)
    created, *_, model = _chain_until_model_generated(project_id)
    failed = created.failed(
        producer="parser", error_type="ValueError", message="kaputt", attempts=3, retryable=True
    )

    await handlers[EventType.STEP_FAILED](failed, ctx)
    project = await _project(db, project_id)
    assert project.status is ProjectStatus.FAILED
    assert project.error is not None
    assert project.error["step"] == "parser"

    # Ein verspätetes model.generated darf ein fehlgeschlagenes Projekt nicht abschließen.
    assert await handlers[EventType.MODEL_GENERATED](model, ctx) is None


async def test_duplicate_delivery_is_ignored(
    db: Database, settings: BackendSettings, ctx: ServiceContext
) -> None:
    handlers = build_handlers(db, settings)
    project_id = await _new_project(db)
    model = _chain_until_model_generated(project_id)[-1]

    first = await handlers[EventType.MODEL_GENERATED](model, ctx)
    second = await handlers[EventType.MODEL_GENERATED](model, ctx)
    assert first is not None
    assert second is None  # kein zweites project.completed

    async with db.sessionmaker() as session:
        count = await session.scalar(select(func.count()).select_from(ProjectEvent))
    assert count == 1


async def test_unknown_project_is_skipped(
    db: Database, settings: BackendSettings, ctx: ServiceContext
) -> None:
    handlers = build_handlers(db, settings)
    event = project_created(uuid.uuid4(), floor_plan_key="plan.dxf")
    assert await handlers[EventType.PROJECT_CREATED](event, ctx) is None
