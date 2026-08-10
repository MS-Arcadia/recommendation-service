from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from arcadia_recommendation.infrastructure.persistence.sql.models import ProcessedEventRow


class SqlProcessedEventLog:
    """A ProcessedEventStore that commits on its own, for the consumer wrapper that runs outside any
    transaction. It marks only after its handler succeeds, so a crash in between leaves the event unmarked and
    it is safely redelivered — at-least-once turning into at-most-once here would silently drop work."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def seen(self, event_id: UUID) -> bool:
        async with self._session_factory() as session:
            statement = select(ProcessedEventRow.event_id).where(ProcessedEventRow.event_id == event_id)
            return (await session.execute(statement)).scalar_one_or_none() is not None

    async def mark(self, event_id: UUID) -> None:
        async with self._session_factory() as session:
            await session.execute(
                insert(ProcessedEventRow)
                .values(event_id=event_id, processed_at=datetime.now(UTC))
                .on_conflict_do_nothing(index_elements=[ProcessedEventRow.event_id])
            )
            await session.commit()
