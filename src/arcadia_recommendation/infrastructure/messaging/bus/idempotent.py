from collections.abc import Awaitable, Callable
from uuid import UUID

from arcadia_recommendation.application.ports.outbound.messaging import ProcessedEventStore
from arcadia_recommendation.infrastructure.messaging.outbox.record import OutboxRecord


class IdempotentHandler:
    """Wraps a consumer with a ProcessedEventStore check so a redelivered event is a no-op. Use cases that
    already guard themselves may skip this; it exists for handlers that cannot."""

    def __init__(
        self,
        store: ProcessedEventStore,
        handler: Callable[[OutboxRecord], Awaitable[None]],
    ) -> None:
        self._store = store
        self._handler = handler

    async def __call__(self, record: OutboxRecord) -> None:
        event_id = _event_id(record)
        if await self._store.seen(event_id):
            return
        await self._handler(record)
        await self._store.mark(event_id)


def _event_id(record: OutboxRecord) -> UUID:
    raw = record.payload.get("event_id")
    return UUID(str(raw)) if raw is not None else record.id
