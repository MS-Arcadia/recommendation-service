from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass(slots=True)
class OutboxRecord:
    """One event staged for publication. `payload` holds the full envelope, `partition_key` decides broker
    ordering, and `correlation_id` rides along so a request can be traced from HTTP entry through the broker
    and into Profile. `id` is what consumers deduplicate on, so it stays the same across the
    retries that make an at-least-once outbox work — a fresh id per attempt would turn every retry into a
    new event."""

    id: UUID
    aggregate_id: UUID
    aggregate_type: str
    event_type: str
    payload: dict[str, Any]
    occurred_at: datetime
    topic: str = "reco-events"
    partition_key: str = ""
    correlation_id: str | None = None
    published_at: datetime | None = None
    attempts: int = 0
    last_error: str | None = None
    next_attempt_at: datetime | None = None
    dead_lettered: bool = False
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def is_pending(self) -> bool:
        return self.published_at is None and not self.dead_lettered
