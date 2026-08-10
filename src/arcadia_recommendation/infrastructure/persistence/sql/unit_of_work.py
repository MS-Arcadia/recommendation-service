from collections.abc import Sequence
from datetime import UTC, datetime
from types import TracebackType
from typing import Self
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from arcadia_recommendation.domain.shared.events import DomainEvent
from arcadia_recommendation.infrastructure.messaging.serialization import event_envelope, partition_key
from arcadia_recommendation.infrastructure.persistence.sql.models import OutboxRow, ProcessedEventRow
from arcadia_recommendation.infrastructure.persistence.sql.repositories import (
    SqlGameProfileRepository,
    SqlOwnershipRepository,
    SqlRecommendationRepository,
    SqlUserPreferenceRepository,
)


class SqlUnitOfWork:
    """One AsyncSession shared by every repository and by the outbox, so the aggregate write and the event row
    land in a single transaction. That property is the whole reason the Outbox pattern works: two sessions
    here would reintroduce exactly the window — committed state, unpublished event — the pattern exists to
    close. Exiting without `commit` rolls back, and the session is closed either way."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        topic: str,
        correlation_id: str | None = None,
    ) -> None:
        self._session = session_factory()
        self._committed = False
        self.games = SqlGameProfileRepository(self._session)
        self.preferences = SqlUserPreferenceRepository(self._session)
        self.ownerships = SqlOwnershipRepository(self._session)
        self.recommendations = SqlRecommendationRepository(self._session)
        self.outbox = SqlOutbox(self._session, topic, correlation_id)
        self.processed_events = SqlProcessedEventStore(self._session)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            if not self._committed:
                await self.rollback()
        finally:
            await self._session.close()

    async def commit(self) -> None:
        await self._session.commit()
        self._committed = True

    async def rollback(self) -> None:
        await self._session.rollback()


class SqlOutbox:
    """Rows are added to the caller's session and never flushed on their own — a publish that could outrun its
    own transaction is the bug this pattern prevents."""

    def __init__(self, session: AsyncSession, topic: str, correlation_id: str | None) -> None:
        self._session = session
        self._topic = topic
        self._correlation_id = correlation_id

    async def enqueue(self, events: Sequence[DomainEvent]) -> None:
        for event in events:
            self._session.add(
                OutboxRow(
                    id=event.event_id,
                    aggregate_id=event.aggregate_id,
                    aggregate_type=event.aggregate_type,
                    event_type=event.event_type,
                    topic=self._topic,
                    partition_key=partition_key(event),
                    payload=event_envelope(event, self._correlation_id),
                    headers={},
                    correlation_id=self._correlation_id,
                    occurred_at=event.occurred_at,
                )
            )


class SqlProcessedEventStore:
    """Consumer-side deduplication inside the caller's transaction. The insert is ON CONFLICT DO NOTHING
    because two replicas can be handed the same redelivery at once, and losing that race should be a no-op
    rather than a failed handler."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def seen(self, event_id: UUID) -> bool:
        statement = select(ProcessedEventRow.event_id).where(ProcessedEventRow.event_id == event_id)
        return (await self._session.execute(statement)).scalar_one_or_none() is not None

    async def mark(self, event_id: UUID) -> None:
        await self._session.execute(
            insert(ProcessedEventRow)
            .values(event_id=event_id, processed_at=datetime.now(UTC))
            .on_conflict_do_nothing(index_elements=[ProcessedEventRow.event_id])
        )
