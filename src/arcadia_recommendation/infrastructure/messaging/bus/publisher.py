from typing import Protocol

from arcadia_recommendation.infrastructure.messaging.outbox.record import OutboxRecord


class EventPublisher(Protocol):
    """The transport the outbox dispatcher drains into. Deliberately not an application port: use cases only
    ever know about OutboxPort, so nothing inward of infrastructure can publish directly.
    The concrete implementation is chosen by MESSAGING_BACKEND: an aiokafka producer on `reco-events`, or
    the in-process bus for a local run."""

    async def publish(self, record: OutboxRecord) -> None: ...
