import asyncio
import contextlib
import json
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

from arcadia_recommendation.infrastructure.messaging.kafka.topics import DLQ_SUFFIX, RETRY_SUFFIX
from arcadia_recommendation.infrastructure.messaging.outbox.record import OutboxRecord
from arcadia_recommendation.infrastructure.observability.logging import get_logger

Handler = Callable[[OutboxRecord], Awaitable[None]]

RECONNECT_SECONDS = 10.0

_logger = get_logger(__name__)


class KafkaSubscriber:
    """Inbound half of the broker: one consumer per topic, all in the `recommendation-service` group,
    routing on `event_type` because the platform's topics carry many. Offsets are committed only after a
    handler succeeds, which is what makes redelivery — and therefore the ProcessedEventStore the handlers
    are wrapped in — a requirement rather than a precaution.

    A handler that keeps failing sends the message to `<topic>.retry` and finally to `<topic>.dlq` (ج-۲)
    instead of blocking the partition: one poison message must not stop every event behind it."""

    def __init__(
        self,
        bootstrap_servers: str,
        group_id: str,
        max_retries: int = 3,
    ) -> None:
        self._bootstrap_servers = bootstrap_servers
        self._group_id = group_id
        self._max_retries = max_retries
        self._handlers: dict[str, dict[str, list[Handler]]] = defaultdict(lambda: defaultdict(list))
        self._consumers: list[AIOKafkaConsumer] = []
        self._tasks: list[asyncio.Task[None]] = []
        self._producer: AIOKafkaProducer | None = None

    def subscribe(self, topic: str, event_type: str, handler: Handler) -> None:
        self._handlers[topic][event_type].append(handler)

    async def start(self) -> None:
        if not await self._connect():
            self._tasks.append(asyncio.create_task(self._retry_connect()))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()
        for consumer in self._consumers:
            await consumer.stop()
        self._consumers.clear()
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None
        _logger.info("kafka_consumers_stopped")

    async def _connect(self) -> bool:
        producer = AIOKafkaProducer(
            bootstrap_servers=self._bootstrap_servers,
            value_serializer=lambda payload: json.dumps(payload, separators=(",", ":")).encode(),
        )
        try:
            await producer.start()
            for topic in self._handlers:
                consumer = AIOKafkaConsumer(
                    topic,
                    bootstrap_servers=self._bootstrap_servers,
                    group_id=f"{self._group_id}-{topic}",
                    value_deserializer=lambda raw: json.loads(raw.decode()),
                    enable_auto_commit=False,
                    auto_offset_reset="earliest",
                )
                await consumer.start()
                self._consumers.append(consumer)
                self._tasks.append(asyncio.create_task(self._run(topic, consumer)))
                _logger.info("kafka_consumer_started", topic=topic, group=f"{self._group_id}-{topic}")
        except Exception as exc:
            _logger.warning("kafka_consumers_unavailable", error=str(exc))
            await self._producer_stop(producer)
            return False
        self._producer = producer
        return True

    async def _retry_connect(self) -> None:
        """Keeps trying rather than giving up at boot. A consumer that silently never subscribes is the worst
        of the three outcomes — worse than crashing, which is at least visible — because the service looks
        healthy while media, catalog and ban events pile up unread."""
        while True:
            await asyncio.sleep(RECONNECT_SECONDS)
            if await self._connect():
                return

    async def _producer_stop(self, producer: AIOKafkaProducer) -> None:
        with contextlib.suppress(Exception):
            await producer.stop()

    async def _run(self, topic: str, consumer: AIOKafkaConsumer) -> None:
        async for message in consumer:
            await self._handle(topic, message.value)
            await consumer.commit()

    async def _handle(self, topic: str, envelope: dict[str, Any]) -> None:
        record = record_from_envelope(envelope, topic)
        handlers = self._handlers[topic].get(record.event_type, [])
        if not handlers:
            return
        for attempt in range(1, self._max_retries + 1):
            try:
                for handler in handlers:
                    await handler(record)
            except Exception as exc:
                _logger.warning(
                    "consumer_handler_failed",
                    topic=topic,
                    event_type=record.event_type,
                    attempt=attempt,
                    error=str(exc),
                )
                if attempt == self._max_retries:
                    await self._reroute(topic, envelope, str(exc))
                    return
                await asyncio.sleep(0.5 * attempt)
            else:
                return

    async def _reroute(self, topic: str, envelope: dict[str, Any], error: str) -> None:
        if self._producer is None:
            return
        target = f"{topic}{DLQ_SUFFIX}" if topic.endswith(RETRY_SUFFIX) else f"{topic}{RETRY_SUFFIX}"
        await self._producer.send_and_wait(target, value={**envelope, "error": error})
        _logger.error("consumer_message_rerouted", topic=topic, target=target, error=error)


def record_from_envelope(envelope: dict[str, Any], topic: str) -> OutboxRecord:
    """The internal representation of one delivered message. Inbound envelopes become the same record the
    in-process bus hands to a handler, so every consumer written against the fake runs unchanged against the
    broker — which is the only reason the fake was worth writing."""
    raw_id = envelope.get("event_id")
    event_id = UUID(str(raw_id)) if raw_id is not None else uuid4()
    raw_aggregate = envelope.get("aggregate_id")
    aggregate_id = UUID(str(raw_aggregate)) if raw_aggregate is not None else event_id
    occurred_at = envelope.get("occurred_at")
    return OutboxRecord(
        id=event_id,
        aggregate_id=aggregate_id,
        aggregate_type=str(envelope.get("aggregate_type", "Unknown")),
        event_type=str(envelope.get("event_type", "")),
        payload=envelope,
        occurred_at=(
            datetime.fromisoformat(str(occurred_at)) if occurred_at is not None else datetime.now(UTC)
        ),
        topic=topic,
        correlation_id=envelope.get("correlation_id"),
    )
