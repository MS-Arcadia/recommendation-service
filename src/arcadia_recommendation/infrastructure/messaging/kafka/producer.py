import json
from typing import Any

from aiokafka import AIOKafkaProducer

from arcadia_recommendation.infrastructure.messaging.kafka.topics import (
    DLQ_SUFFIX,
    RETRY_SUFFIX,
    ensure_topics,
)
from arcadia_recommendation.infrastructure.messaging.outbox.record import OutboxRecord
from arcadia_recommendation.infrastructure.observability.logging import get_logger

_logger = get_logger(__name__)


class KafkaEventPublisher:
    """The transport the outbox dispatcher drains into. `acks=all` plus an idempotent producer is what makes
    a retry safe: without idempotence, a send that succeeded but whose acknowledgement was lost would be
    written twice, and the dispatcher retries by design. The partition key is the record's own, so one user's
    generated sets stay mutually ordered for Profile.

    An unreachable broker at boot is logged, not fatal. The outbox is what makes that safe: events accumulate
    in the table and drain when the broker returns, so refusing to start would trade a recoverable delay in
    publication for an outright outage of the recommendation reads."""

    def __init__(self, bootstrap_servers: str, topic: str) -> None:
        self._bootstrap_servers = bootstrap_servers
        self._topic = topic
        self._producer: AIOKafkaProducer | None = None

    async def start(self) -> None:
        await ensure_topics(
            self._bootstrap_servers,
            (self._topic, f"{self._topic}{RETRY_SUFFIX}", f"{self._topic}{DLQ_SUFFIX}"),
        )
        await self._connect()

    async def stop(self) -> None:
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None
            _logger.info("kafka_producer_stopped")

    async def publish(self, record: OutboxRecord) -> None:
        if self._producer is None and not await self._connect():
            raise ConnectionError("kafka producer is not connected")
        producer = self._producer
        if producer is None:
            raise ConnectionError("kafka producer is not connected")
        await producer.send_and_wait(
            record.topic, value=record.payload, key=record.partition_key or str(record.aggregate_id)
        )

    async def _connect(self) -> bool:
        producer = AIOKafkaProducer(
            bootstrap_servers=self._bootstrap_servers,
            value_serializer=_serialize,
            key_serializer=lambda key: key.encode() if key else None,
            enable_idempotence=True,
            acks="all",
        )
        try:
            await producer.start()
        except Exception as exc:
            _logger.warning("kafka_producer_unavailable", error=str(exc))
            return False
        self._producer = producer
        _logger.info("kafka_producer_started", bootstrap_servers=self._bootstrap_servers)
        return True


def _serialize(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode()
