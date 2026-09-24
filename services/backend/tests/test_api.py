from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from typing import cast

import pytest
from fakeredis import FakeAsyncRedis, FakeServer
from fastapi.testclient import TestClient
from moto import mock_aws

from lumira_backend.config import BackendSettings
from lumira_backend.db import Database
from lumira_backend.main import create_app
from lumira_shared import EventType, S3Storage, stream_name


def _wait_until(predicate: Callable[[], bool], timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


class Stack:
    def __init__(self, client: TestClient, redis: FakeAsyncRedis, storage: S3Storage) -> None:
        self.client = client
        self.redis = redis
        self.storage = storage
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
            s = Stack(client, redis, storage)
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
