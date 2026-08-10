from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from arcadia_recommendation.domain.shared.events import DomainEvent


class OutboxPort(Protocol):
    """Events are handed here, never published directly. The implementation must write them inside the same
    transaction as the aggregate change, which is what makes "no lost events" true rather than hopeful."""

    async def enqueue(self, events: Sequence[DomainEvent]) -> None: ...


class ProcessedEventStore(Protocol):
    """Deduplication for inbound consumers: at-least-once delivery means an event_id will arrive twice."""

    async def seen(self, event_id: UUID) -> bool: ...

    async def mark(self, event_id: UUID) -> None: ...
