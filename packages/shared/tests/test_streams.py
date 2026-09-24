from __future__ import annotations

from typing import cast
from uuid import UUID

import pytest
from fakeredis import FakeAsyncRedis

from lumira_shared.events import (
    DEAD_LETTER_STREAM,
    Artifact,
    Event,
    EventType,
    project_created,
    stream_name,
)
from lumira_shared.streams import (
    ConsumerOptions,
    NonRetryableError,
    StreamConsumer,
    StreamPublisher,
)

# Nicht blockieren und liegengebliebene Nachrichten sofort neu zustellen.
FAST = ConsumerOptions(block_ms=None, claim_idle_ms=0, max_attempts=3)


def _consumer(redis: FakeAsyncRedis, handlers: dict, *, group: str = "parser") -> StreamConsumer:
    return StreamConsumer(
        redis,
        group=group,
        consumer=f"{group}-1",
        handlers=handlers,
        publisher=StreamPublisher(redis),
        options=FAST,
    )


async def _events(redis: FakeAsyncRedis, event_type: EventType) -> list[Event]:
    # decode_responses=True → str statt bytes; die redis-Stubs kennen das nicht.
    entries = cast("list[tuple[str, dict[str, str]]]", await redis.xrange(stream_name(event_type)))
    return [Event.from_stream_fields(fields) for _, fields in entries]


async def _pending(redis: FakeAsyncRedis, event_type: EventType, group: str = "parser") -> int:
    info = await redis.xpending(stream_name(event_type), group)
    return int(info["pending"])


async def test_handler_result_is_published_and_message_acked(
    redis: FakeAsyncRedis, project_id: UUID
) -> None:
    async def parse(event: Event) -> Event:
        return event.follow_up(
            EventType.PLAN_PARSED, producer="parser", artifacts={Artifact.PARSED_PLAN: "p.json"}
        )

    publisher = StreamPublisher(redis)
    # Absichtlich VOR dem Anlegen der Consumer Group publiziert – darf nicht verloren gehen.
    created = project_created(project_id, floor_plan_key="plan.pdf")
    await publisher.publish(created)

    consumer = _consumer(redis, {EventType.PROJECT_CREATED: parse})
    await consumer.ensure_groups()
    assert await consumer.poll_once() == 1

    [parsed] = await _events(redis, EventType.PLAN_PARSED)
    assert parsed.causation_id == created.event_id
    assert parsed.artifacts[Artifact.PARSED_PLAN] == "p.json"
    assert await _pending(redis, EventType.PROJECT_CREATED) == 0


async def test_ensure_groups_is_idempotent(redis: FakeAsyncRedis) -> None:
    async def noop(event: Event) -> None:
        return None

    consumer = _consumer(redis, {EventType.PROJECT_CREATED: noop})
    await consumer.ensure_groups()
    await consumer.ensure_groups()


async def test_retries_then_publishes_step_failed(redis: FakeAsyncRedis, project_id: UUID) -> None:
    calls = 0

    async def flaky(event: Event) -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("OCR-Modell nicht geladen")

    await StreamPublisher(redis).publish(project_created(project_id, floor_plan_key="plan.pdf"))
    consumer = _consumer(redis, {EventType.PROJECT_CREATED: flaky})
    await consumer.ensure_groups()

    for _ in range(3):
        await consumer.poll_once()

    assert calls == 3
    [failed] = await _events(redis, EventType.STEP_FAILED)
    assert failed.error is not None
    assert failed.error.step == "parser"
    assert failed.error.attempts == 3
    assert failed.error.retryable is True
    assert failed.error.error_type == "RuntimeError"
    assert await _pending(redis, EventType.PROJECT_CREATED) == 0
    assert await redis.xlen(DEAD_LETTER_STREAM) == 1

    # Nach dem endgültigen Fehlschlag wird nichts mehr zugestellt.
    assert await consumer.poll_once() == 0
    assert calls == 3


async def test_non_retryable_error_fails_immediately(
    redis: FakeAsyncRedis, project_id: UUID
) -> None:
    async def broken_input(event: Event) -> None:
        raise NonRetryableError("PDF ist verschlüsselt")

    await StreamPublisher(redis).publish(project_created(project_id, floor_plan_key="plan.pdf"))
    consumer = _consumer(redis, {EventType.PROJECT_CREATED: broken_input})
    await consumer.ensure_groups()
    await consumer.poll_once()

    [failed] = await _events(redis, EventType.STEP_FAILED)
    assert failed.error is not None
    assert failed.error.attempts == 1
    assert failed.error.retryable is False


async def test_unparseable_message_goes_to_dead_letter(redis: FakeAsyncRedis) -> None:
    async def never_called(event: Event) -> None:
        pytest.fail("Handler darf für defekte Nachrichten nicht aufgerufen werden")

    consumer = _consumer(redis, {EventType.PROJECT_CREATED: never_called})
    await consumer.ensure_groups()
    await redis.xadd(stream_name(EventType.PROJECT_CREATED), {"event": "{kein json"})

    assert await consumer.poll_once() == 1
    assert await redis.xlen(DEAD_LETTER_STREAM) == 1
    assert await _pending(redis, EventType.PROJECT_CREATED) == 0


async def test_failing_step_failed_handler_does_not_loop(
    redis: FakeAsyncRedis, project_id: UUID
) -> None:
    async def failing(event: Event) -> None:
        raise NonRetryableError("DB weg")

    created = project_created(project_id, floor_plan_key="plan.pdf")
    failed = created.failed(
        producer="parser", error_type="X", message="y", attempts=1, retryable=False
    )
    await StreamPublisher(redis).publish(failed)

    consumer = _consumer(redis, {EventType.STEP_FAILED: failing}, group="backend")
    await consumer.ensure_groups()
    await consumer.poll_once()

    assert await redis.xlen(stream_name(EventType.STEP_FAILED)) == 1  # kein neues step.failed
    assert await _pending(redis, EventType.STEP_FAILED, group="backend") == 0


async def test_two_groups_each_receive_the_event(redis: FakeAsyncRedis, project_id: UUID) -> None:
    seen: list[str] = []

    def recorder(name: str):
        async def handle(event: Event) -> None:
            seen.append(name)

        return handle

    backend = _consumer(redis, {EventType.PROJECT_CREATED: recorder("backend")}, group="backend")
    parser = _consumer(redis, {EventType.PROJECT_CREATED: recorder("parser")}, group="parser")
    await backend.ensure_groups()
    await parser.ensure_groups()
    await StreamPublisher(redis).publish(project_created(project_id, floor_plan_key="plan.pdf"))

    await backend.poll_once()
    await parser.poll_once()
    assert sorted(seen) == ["backend", "parser"]
