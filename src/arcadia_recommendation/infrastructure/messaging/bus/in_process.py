import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable

from arcadia_recommendation.infrastructure.messaging.outbox.record import OutboxRecord

Handler = Callable[[OutboxRecord], Awaitable[None]]


class InProcessEventBus:
    """Stands in for Kafka and mimics its awkward parts on purpose: delivery may repeat, and ordering holds
    only per aggregate rather than globally. A bus that were nicer than Kafka would hide consumer bugs until
    the swap. `topic` is accepted and not used: this bus has one channel, and taking the argument is what
    lets a subscription read identically here and against the broker."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        self._delivered: list[OutboxRecord] = []
        self._down = False

    def subscribe(self, topic: str, event_type: str, handler: Handler) -> None:
        self._handlers[event_type].append(handler)

    def force_down(self, down: bool = True) -> None:
        self._down = down

    @property
    def delivered(self) -> list[OutboxRecord]:
        return list(self._delivered)

    async def publish(self, record: OutboxRecord) -> None:
        if self._down:
            raise ConnectionError("event bus is unavailable")
        self._delivered.append(record)
        handlers = self._handlers.get(record.event_type, [])
        if handlers:
            await asyncio.gather(*(handler(record) for handler in handlers))
