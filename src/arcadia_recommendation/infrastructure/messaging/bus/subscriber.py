from collections.abc import Awaitable, Callable
from typing import Protocol

from arcadia_recommendation.infrastructure.messaging.outbox.record import OutboxRecord

Handler = Callable[[OutboxRecord], Awaitable[None]]


class EventSubscriber(Protocol):
    """What the consumer registry needs from inbound messaging, so registering a handler reads the same
    whether it lands on the in-process bus or on a Kafka consumer group."""

    def subscribe(self, topic: str, event_type: str, handler: Handler) -> None: ...
