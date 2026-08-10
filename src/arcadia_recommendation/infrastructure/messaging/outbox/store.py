from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from arcadia_recommendation.infrastructure.messaging.outbox.record import OutboxRecord


class OutboxStore(Protocol):
    """Where the dispatcher reads pending events and writes back the outcome of a publish attempt. Introduced
    so the same dispatcher — retry, backoff, dead-lettering and all — runs against dicts in a test and against
    a table in production; the pattern is in the dispatcher, not in the storage.

    The two counts are synchronous because they feed observable metric gauges, which are scraped on a
    callback and cannot await. An implementation backed by a database therefore serves them from the last
    poll rather than by querying per scrape."""

    @property
    def pending_count(self) -> int: ...

    @property
    def dlq_depth(self) -> int: ...

    async def due(self, now: datetime, limit: int) -> Sequence[OutboxRecord]: ...

    async def record_published(self, record: OutboxRecord) -> None: ...

    async def record_failure(self, record: OutboxRecord) -> None: ...
