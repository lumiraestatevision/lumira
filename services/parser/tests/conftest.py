from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
from fakeredis import FakeAsyncRedis, FakeServer
from moto import mock_aws

from lumira_parser.config import ParserSettings
from lumira_shared import S3Storage, ServiceContext, StreamPublisher, get_logger


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
        settings=ParserSettings(_env_file=None),  # pyright: ignore[reportCallIssue]
        redis=redis,
        publisher=StreamPublisher(redis),
        storage=storage,
        log=get_logger("test"),
    )
    await redis.aclose()
