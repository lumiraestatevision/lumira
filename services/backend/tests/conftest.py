from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from pathlib import Path

import pytest
from fakeredis import FakeAsyncRedis, FakeServer

from lumira_backend.config import BackendSettings
from lumira_backend.db import Database


@pytest.fixture
def aws_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-central-1")
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    monkeypatch.delenv("AWS_ENDPOINT_URL_S3", raising=False)


def make_settings(tmp_path: Path, **overrides: object) -> BackendSettings:
    values: dict[str, object] = {
        "database_url": f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        "environment": "test",
        "log_json": False,
        "consumer_block_ms": None,
        "s3_bucket": "lumira-test",
        "pipeline_vr_enabled": False,
        "max_upload_mb": 1,
    }
    values.update(overrides)
    # _env_file=None: Tests dürfen nicht von einer lokalen .env abhängen.
    return BackendSettings(_env_file=None, **values)  # pyright: ignore[reportCallIssue]


@pytest.fixture
def settings_factory(tmp_path: Path) -> Callable[..., BackendSettings]:
    return lambda **overrides: make_settings(tmp_path, **overrides)


@pytest.fixture
def settings(tmp_path: Path) -> BackendSettings:
    return make_settings(tmp_path)


@pytest.fixture
async def db(settings: BackendSettings) -> AsyncIterator[Database]:
    database = Database(settings.database_url)
    await database.create_all()
    yield database
    await database.dispose()


@pytest.fixture
async def redis() -> AsyncIterator[FakeAsyncRedis]:
    client = FakeAsyncRedis(server=FakeServer(), decode_responses=True)
    yield client
    await client.aclose()
