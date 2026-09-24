"""Redis Streams: Publisher und Consumer mit Consumer Groups.

Zustellgarantie: *at-least-once*. Ein Event wird erst nach erfolgreicher Verarbeitung
und nach dem Publizieren der Folgeevents bestätigt (XACK). Handler müssen deshalb
idempotent sein – z. B. indem sie ihre Ergebnisse unter deterministischen S3-Keys ablegen.

Fehlerbehandlung:
- Exception im Handler: Die Nachricht bleibt *pending* und wird nach ``claim_idle_ms``
  per XAUTOCLAIM erneut zugestellt (auch an einen anderen Consumer derselben Gruppe).
- Nach ``max_attempts`` Zustellungen oder bei ``NonRetryableError``: ``step.failed``
  wird publiziert, die Nachricht bestätigt und in den Dead-Letter-Stream kopiert.
- Nicht parsebare Nachrichten landen direkt im Dead-Letter-Stream.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import structlog
from pydantic import ValidationError
from redis.asyncio import Redis
from redis.exceptions import ResponseError

from lumira_shared.events import DEAD_LETTER_STREAM, STREAM_PREFIX, Event, EventType, stream_name
from lumira_shared.log import get_logger

log = get_logger(__name__)

HandlerResult = Event | Sequence[Event] | None
EventHandler = Callable[[Event], Awaitable[HandlerResult]]

Message = tuple[str, dict[str, str]]


class NonRetryableError(Exception):
    """Fehler, bei dem ein erneuter Versuch sinnlos ist (z. B. defekte Eingabedatei)."""


@dataclass(frozen=True, slots=True)
class ConsumerOptions:
    batch_size: int = 10
    block_ms: int | None = 5_000  # None = nicht blockieren, stattdessen idle_sleep_s pausieren
    claim_idle_ms: int = 30_000
    max_attempts: int = 3
    error_backoff_s: float = 2.0
    idle_sleep_s: float = 0.1


class StreamPublisher:
    def __init__(
        self, redis: Redis, *, prefix: str = STREAM_PREFIX, maxlen: int | None = 100_000
    ) -> None:
        self._redis = redis
        self._prefix = prefix
        self._maxlen = maxlen

    async def publish(self, event: Event) -> str:
        stream = stream_name(event.type, self._prefix)
        message_id = await self._redis.xadd(
            stream,
            event.to_stream_fields(),  # pyright: ignore[reportArgumentType]
            maxlen=self._maxlen,
            approximate=True,
        )
        log.info(
            "event.published",
            event_type=str(event.type),
            event_id=str(event.event_id),
            project_id=str(event.project_id),
            stream=stream,
            message_id=message_id,
        )
        return str(message_id)


class StreamConsumer:
    def __init__(
        self,
        redis: Redis,
        *,
        group: str,
        consumer: str,
        handlers: Mapping[EventType, EventHandler],
        publisher: StreamPublisher,
        options: ConsumerOptions | None = None,
        prefix: str = STREAM_PREFIX,
    ) -> None:
        if not handlers:
            raise ValueError("Ein Consumer braucht mindestens einen Handler")
        self._redis = redis
        self._group = group
        self._consumer = consumer
        self._handlers = dict(handlers)
        self._publisher = publisher
        self._options = options or ConsumerOptions()
        self._streams: dict[str, EventType] = {stream_name(t, prefix): t for t in handlers}
        self.last_poll_at: float | None = None  # time.monotonic() des letzten Polls

    @property
    def streams(self) -> list[str]:
        return list(self._streams)

    async def ensure_groups(self) -> None:
        """Legt die Consumer Group je Stream an. Start bei ID 0: Auch Events, die vor dem
        ersten Start dieses Services publiziert wurden, werden verarbeitet."""
        for stream in self._streams:
            try:
                await self._redis.xgroup_create(stream, self._group, id="0", mkstream=True)
            except ResponseError as exc:
                if "BUSYGROUP" not in str(exc):
                    raise

    async def run(self, stop: asyncio.Event) -> None:
        log.info(
            "consumer.starting", group=self._group, consumer=self._consumer, streams=self.streams
        )
        while not stop.is_set():
            try:
                await self.ensure_groups()
                break
            except Exception:
                log.exception("consumer.group_setup_failed")
                await _sleep_unless_stopped(stop, self._options.error_backoff_s)

        while not stop.is_set():
            try:
                handled = await self.poll_once()
                if handled == 0 and self._options.block_ms is None:
                    await _sleep_unless_stopped(stop, self._options.idle_sleep_s)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("consumer.poll_failed")
                await _sleep_unless_stopped(stop, self._options.error_backoff_s)
        log.info("consumer.stopped", group=self._group)

    async def poll_once(self) -> int:
        """Ein Durchlauf: liegengebliebene Nachrichten übernehmen, dann neue lesen."""
        self.last_poll_at = time.monotonic()
        handled = 0
        for stream in self._streams:
            for message in await self._claim_stale(stream):
                await self._handle(stream, *message)
                handled += 1

        response = await self._redis.xreadgroup(
            self._group,
            self._consumer,
            streams={s: ">" for s in self._streams},
            count=self._options.batch_size,
            block=self._options.block_ms,
        )
        for stream, messages in _iter_stream_response(response):
            for message_id, fields in messages:
                await self._handle(stream, message_id, fields)
                handled += 1
        return handled

    async def _claim_stale(self, stream: str) -> list[Message]:
        result = await self._redis.xautoclaim(
            stream,
            self._group,
            self._consumer,
            min_idle_time=self._options.claim_idle_ms,
            start_id="0-0",
            count=self._options.batch_size,
        )
        claimed = result[1] if result else []
        # Bereits gelöschte Einträge liefert Redis mit fields=None.
        return [(_decode(mid), fields) for mid, fields in claimed if fields]

    async def _handle(self, stream: str, message_id: str, fields: dict[str, str]) -> None:
        try:
            event = Event.from_stream_fields(fields)
        except (KeyError, ValueError, ValidationError) as exc:
            log.error("event.unparseable", stream=stream, message_id=message_id, error=str(exc))
            await self._dead_letter(stream, message_id, fields, reason=f"unparseable: {exc}")
            await self._redis.xack(stream, self._group, message_id)
            return

        handler = self._handlers[self._streams[stream]]
        with structlog.contextvars.bound_contextvars(
            event_id=str(event.event_id),
            event_type=str(event.type),
            project_id=str(event.project_id),
        ):
            started = time.perf_counter()
            try:
                result = await handler(event)
            except Exception as exc:
                await self._on_failure(stream, message_id, fields, event, exc)
                return

            produced = _as_list(result)
            for follow_up in produced:
                await self._publisher.publish(follow_up)
            await self._redis.xack(stream, self._group, message_id)
            log.info(
                "event.processed",
                produced=[str(e.type) for e in produced],
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
            )

    async def _on_failure(
        self,
        stream: str,
        message_id: str,
        fields: dict[str, str],
        event: Event,
        exc: Exception,
    ) -> None:
        retryable = not isinstance(exc, NonRetryableError)
        attempts = await self._delivery_count(stream, message_id)

        if retryable and attempts < self._options.max_attempts:
            log.warning(
                "event.retry_scheduled",
                attempt=attempts,
                max_attempts=self._options.max_attempts,
                retry_after_ms=self._options.claim_idle_ms,
                error=repr(exc),
            )
            return  # bleibt pending → XAUTOCLAIM stellt erneut zu

        log.error("event.failed", attempts=attempts, retryable=retryable, exc_info=exc)
        # Ein fehlgeschlagener step.failed-Handler erzeugt kein weiteres step.failed (Endlosschleife).
        if event.type is not EventType.STEP_FAILED:
            await self._publisher.publish(
                event.failed(
                    producer=self._group,
                    error_type=type(exc).__name__,
                    message=str(exc)[:2000] or type(exc).__name__,
                    attempts=attempts,
                    retryable=retryable,
                )
            )
        await self._dead_letter(stream, message_id, fields, reason=f"{type(exc).__name__}: {exc}")
        await self._redis.xack(stream, self._group, message_id)

    async def _delivery_count(self, stream: str, message_id: str) -> int:
        entries = await self._redis.xpending_range(
            stream, self._group, min=message_id, max=message_id, count=1
        )
        return int(entries[0]["times_delivered"]) if entries else 1

    async def _dead_letter(
        self, stream: str, message_id: str, fields: Mapping[str, str], *, reason: str
    ) -> None:
        await self._redis.xadd(
            DEAD_LETTER_STREAM,
            {
                "stream": stream,
                "message_id": message_id,
                "group": self._group,
                "reason": reason[:2000],
                "event": fields.get("event", ""),
            },
            maxlen=10_000,
            approximate=True,
        )


def _as_list(result: HandlerResult) -> list[Event]:
    if result is None:
        return []
    if isinstance(result, Event):
        return [result]
    return list(result)


def _decode(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def _iter_stream_response(response: Any) -> Iterable[tuple[str, list[Message]]]:
    """Normalisiert XREADGROUP-Antworten von RESP2 (Liste) und RESP3 (Dict)."""
    if not response:
        return []
    items = response.items() if isinstance(response, dict) else response
    return [
        (_decode(stream), [(_decode(mid), fields) for mid, fields in messages if fields])
        for stream, messages in items
    ]


async def _sleep_unless_stopped(stop: asyncio.Event, seconds: float) -> None:
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), timeout=seconds)
