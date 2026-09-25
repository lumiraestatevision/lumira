from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from fakeredis import FakeAsyncRedis, FakeServer
from fastapi.testclient import TestClient
from moto import mock_aws

from lumira_backend.config import BackendSettings
from lumira_backend.db import Database, Project, ProjectStatus
from lumira_backend.main import create_app
from lumira_shared import (
    Event,
    EventType,
    S3Storage,
    StreamPublisher,
    project_created,
    stream_name,
)


def _wait_until(predicate: Callable[[], bool], timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


class Stack:
    def __init__(
        self, client: TestClient, redis: FakeAsyncRedis, storage: S3Storage, db: Database
    ) -> None:
        self.client = client
        self.redis = redis
        self.storage = storage
        self.db = db
        portal = client.portal
        assert portal is not None
        self.portal = portal


@pytest.fixture
def stack(settings: BackendSettings, aws_env: None) -> Iterator[Stack]:
    with mock_aws():
        redis = FakeAsyncRedis(server=FakeServer(), decode_responses=True)
        storage = S3Storage(settings.s3_bucket)
        db = Database(settings.database_url)
        app = create_app(settings, db=db, redis=redis, storage=storage)
        with TestClient(app) as client:
            s = Stack(client, redis, storage, db)
            s.portal.call(db.create_all)
            s.portal.call(storage.ensure_bucket)
            yield s


def _upload(stack: Stack, **files: tuple[str, bytes, str]):
    files.setdefault("floor_plan", ("grundriss.dxf", b"0\nEOF\n", "application/dxf"))
    return stack.client.post("/projects", data={"name": "Musterhaus"}, files=files)


def test_health_includes_database(stack: Stack) -> None:
    ready = stack.client.get("/health/ready")
    assert ready.status_code == 200
    assert ready.json()["checks"] == {"redis": True, "database": True, "consumer": True}


def test_upload_creates_project_and_publishes_event(stack: Stack) -> None:
    response = _upload(stack, blv=("lv.pdf", b"%PDF-1.7 test", "application/pdf"))
    assert response.status_code == 201, response.text
    project = response.json()
    assert project["status"] == "processing"
    assert set(project["artifacts"]) == {"floor_plan_source", "blv_source"}

    plan_key = project["artifacts"]["floor_plan_source"]
    assert plan_key.endswith("/upload/floor_plan.dxf")
    assert stack.portal.call(stack.storage.get_bytes, plan_key) == b"0\nEOF\n"
    assert stack.portal.call(stack.redis.xlen, stream_name(EventType.PROJECT_CREATED)) == 1

    # Der eigene Consumer des backends protokolliert project.created in der Timeline.
    def has_event() -> bool:
        detail = stack.client.get(f"/projects/{project['id']}").json()
        return [e["type"] for e in detail["events"]] == ["project.created"]

    assert _wait_until(has_event)

    listed = stack.client.get("/projects").json()
    assert [p["id"] for p in listed] == [project["id"]]

    download = stack.client.get(f"/projects/{project['id']}/artifacts/blv_source")
    assert download.status_code == 200
    assert download.content == b"%PDF-1.7 test"
    assert download.headers["content-type"] == "application/pdf"


def test_rejects_wrong_file_type(stack: Stack) -> None:
    response = _upload(stack, floor_plan=("grundriss.docx", b"x", "application/octet-stream"))
    assert response.status_code == 422
    assert "nicht erlaubt" in cast(str, response.json()["detail"])


def test_rejects_too_large_upload(stack: Stack) -> None:
    too_big = b"0" * (1024 * 1024 + 1)  # max_upload_mb=1 in den Test-Settings
    response = _upload(stack, floor_plan=("grundriss.pdf", too_big, "application/pdf"))
    assert response.status_code == 413


def test_unknown_project_is_404(stack: Stack) -> None:
    assert stack.client.get("/projects/00000000-0000-0000-0000-000000000000").status_code == 404
    assert stack.client.delete("/projects/00000000-0000-0000-0000-000000000000").status_code == 404


def _set_status(stack: Stack, project_id: str, status: ProjectStatus, age_min: int = 0) -> None:
    async def update() -> None:
        async with stack.db.sessionmaker() as session, session.begin():
            project = await session.get_one(Project, uuid.UUID(project_id))
            project.status = status
            project.updated_at = datetime.now(UTC) - timedelta(minutes=age_min)

    stack.portal.call(update)


def test_delete_removes_project_events_and_files(stack: Stack) -> None:
    project = _upload(stack, blv=("lv.pdf", b"%PDF-1.7 test", "application/pdf")).json()
    pid = project["id"]
    keys = list(project["artifacts"].values())
    assert _wait_until(lambda: bool(stack.client.get(f"/projects/{pid}").json()["events"]))

    # Läuft noch → abgelehnt, nichts gelöscht
    assert stack.client.delete(f"/projects/{pid}").status_code == 409
    assert stack.portal.call(stack.storage.exists, keys[0])

    _set_status(stack, pid, ProjectStatus.COMPLETED)
    assert stack.client.delete(f"/projects/{pid}").status_code == 204

    assert stack.client.get(f"/projects/{pid}").status_code == 404
    assert stack.client.get("/projects").json() == []
    assert not any(stack.portal.call(stack.storage.exists, key) for key in keys)


def test_rerun_keeps_uploads_and_restarts_pipeline(stack: Stack) -> None:
    project = _upload(stack, blv=("lv.pdf", b"%PDF-1.7 test", "application/pdf")).json()
    pid = project["id"]
    assert _wait_until(lambda: bool(stack.client.get(f"/projects/{pid}").json()["events"]))
    assert stack.client.post(f"/projects/{pid}/rerun").status_code == 409  # läuft noch

    derived = f"projects/{pid}/generator/model.glb"
    stack.portal.call(stack.storage.put_bytes, derived, b"alt")
    _set_status(stack, pid, ProjectStatus.FAILED)

    response = stack.client.post(f"/projects/{pid}/rerun")

    assert response.status_code == 202, response.text
    rerun = response.json()
    assert rerun["id"] == pid  # dasselbe Projekt, kein neues
    assert rerun["status"] == "processing"
    assert rerun["error"] is None
    assert set(rerun["artifacts"]) == {"floor_plan_source", "blv_source"}
    assert not stack.portal.call(stack.storage.exists, derived)  # alte Ergebnisse weg
    assert stack.portal.call(stack.storage.exists, project["artifacts"]["blv_source"])
    assert stack.portal.call(stack.redis.xlen, stream_name(EventType.PROJECT_CREATED)) == 2
    assert [p["id"] for p in stack.client.get("/projects").json()] == [pid]

    # Ein verspätetes Event des ALTEN Durchlaufs darf den neuen Verlauf nicht verfälschen.
    plan_key = project["artifacts"]["floor_plan_source"]
    old_run = project_created(uuid.UUID(pid), floor_plan_key=plan_key)
    stale = old_run.follow_up(
        EventType.MODEL_GENERATED,
        producer="generator",
        artifacts={"model_fbx": "x.fbx", "model_gltf": "x.glb"},
    )

    async def current_run() -> uuid.UUID | None:
        async with stack.db.sessionmaker() as session:
            return (await session.get_one(Project, uuid.UUID(pid))).run_id

    run_id = stack.portal.call(current_run)
    assert run_id is not None
    this_run = Event(
        event_id=run_id,
        run_id=run_id,
        type=EventType.PROJECT_CREATED,
        project_id=uuid.UUID(pid),
        producer="backend",
        artifacts={"floor_plan_source": plan_key},
    )
    publisher = StreamPublisher(stack.redis)
    stack.portal.call(publisher.publish, stale)
    stack.portal.call(
        publisher.publish,
        this_run.follow_up(
            EventType.PLAN_PARSED, producer="parser", artifacts={"parsed_plan": "p.json"}
        ),
    )
    assert _wait_until(lambda: "plan.parsed" in _types(stack, pid))  # andere Events kommen an …
    detail = stack.client.get(f"/projects/{pid}").json()
    assert "model.generated" not in _types(stack, pid)  # … das alte nicht
    assert detail["status"] == "processing"


def _types(stack: Stack, pid: str) -> list[str]:
    return [e["type"] for e in stack.client.get(f"/projects/{pid}").json()["events"]]


def test_stuck_project_can_be_deleted(stack: Stack) -> None:
    pid = _upload(stack).json()["id"]
    _set_status(stack, pid, ProjectStatus.PROCESSING, age_min=45)  # hängt seit 45 min
    assert stack.client.delete(f"/projects/{pid}").status_code == 204
