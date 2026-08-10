from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from arcadia_recommendation.infrastructure.messaging.outbox.record import OutboxRecord
from arcadia_recommendation.infrastructure.persistence.memory.store import MemoryStore


class MemoryOutboxStore:
    """The dispatcher's view of the in-memory outbox. Records are the same objects the unit of work
    committed, so recording an outcome is a field assignment rather than a write-back."""

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    @property
    def pending_count(self) -> int:
        return sum(1 for record in self._store.outbox.values() if record.is_pending)

    @property
    def dlq_depth(self) -> int:
        return sum(1 for record in self._store.outbox.values() if record.dead_lettered)

    async def due(self, now: datetime, limit: int) -> Sequence[OutboxRecord]:
        ready = [
            record
            for record in self._store.outbox.values()
            if record.is_pending and (record.next_attempt_at is None or record.next_attempt_at <= now)
        ]
        ready.sort(key=lambda record: record.occurred_at)
        return ready[:limit]

    async def record_published(self, record: OutboxRecord) -> None:
        self._store.outbox[record.id] = record

    async def record_failure(self, record: OutboxRecord) -> None:
        self._store.outbox[record.id] = record


class MemoryProcessedEventLog:
    """The standalone ProcessedEventStore the consumer wrapper uses, outside any unit of work."""

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    async def seen(self, event_id: UUID) -> bool:
        return event_id in self._store.processed_events

    async def mark(self, event_id: UUID) -> None:
        self._store.processed_events.add(event_id)
