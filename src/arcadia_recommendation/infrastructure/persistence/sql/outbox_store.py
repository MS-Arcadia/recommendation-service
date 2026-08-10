from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from arcadia_recommendation.infrastructure.messaging.outbox.record import OutboxRecord
from arcadia_recommendation.infrastructure.persistence.sql.mapping import aware
from arcadia_recommendation.infrastructure.persistence.sql.models import OutboxRow


class SqlOutboxStore:
    """The relay half of the Outbox pattern, reading the table the unit of work writes. A poll claims rows
    with SKIP LOCKED and leases them by pushing `next_attempt_at` forward before the publish begins, so two
    replicas draining the same outbox do not both send the same row. The lease expires on its own: a replica
    that dies mid-publish leaves the row to the next poll, which is why the contract stays at-least-once
    rather than becoming at-most-once. The gauge counts are refreshed here rather than per metric scrape,
    because an observable gauge callback cannot await."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        batch_size: int = 50,
        lease: timedelta = timedelta(seconds=30),
    ) -> None:
        self._session_factory = session_factory
        self._batch_size = batch_size
        self._lease = lease
        self._pending = 0
        self._dead_lettered = 0

    @property
    def pending_count(self) -> int:
        return self._pending

    @property
    def dlq_depth(self) -> int:
        return self._dead_lettered

    async def due(self, now: datetime, limit: int) -> Sequence[OutboxRecord]:
        async with self._session_factory() as session:
            statement = (
                select(OutboxRow)
                .where(
                    OutboxRow.published_at.is_(None),
                    OutboxRow.dead_lettered.is_(False),
                    (OutboxRow.next_attempt_at.is_(None)) | (OutboxRow.next_attempt_at <= now),
                )
                .order_by(OutboxRow.occurred_at)
                .limit(min(limit, self._batch_size))
                .with_for_update(skip_locked=True)
            )
            rows = (await session.execute(statement)).scalars().all()
            records = [_to_record(row) for row in rows]
            for row in rows:
                row.next_attempt_at = now + self._lease
            await self._refresh_counts(session)
            await session.commit()
        return records

    async def record_published(self, record: OutboxRecord) -> None:
        await self._apply(
            record,
            published_at=record.published_at,
            attempts=record.attempts,
            last_error=None,
        )

    async def record_failure(self, record: OutboxRecord) -> None:
        await self._apply(
            record,
            attempts=record.attempts,
            last_error=record.last_error,
            next_attempt_at=record.next_attempt_at,
            dead_lettered=record.dead_lettered,
        )

    async def _apply(self, record: OutboxRecord, **values: object) -> None:
        async with self._session_factory() as session:
            await session.execute(update(OutboxRow).where(OutboxRow.id == record.id).values(**values))
            await session.commit()

    async def _refresh_counts(self, session: AsyncSession) -> None:
        pending = await session.scalar(
            select(func.count())
            .select_from(OutboxRow)
            .where(OutboxRow.published_at.is_(None), OutboxRow.dead_lettered.is_(False))
        )
        dead = await session.scalar(
            select(func.count()).select_from(OutboxRow).where(OutboxRow.dead_lettered.is_(True))
        )
        self._pending = int(pending or 0)
        self._dead_lettered = int(dead or 0)


def _to_record(row: OutboxRow) -> OutboxRecord:
    return OutboxRecord(
        id=row.id,
        aggregate_id=row.aggregate_id,
        aggregate_type=row.aggregate_type,
        event_type=row.event_type,
        payload=row.payload,
        occurred_at=aware(row.occurred_at),
        topic=row.topic,
        partition_key=row.partition_key,
        correlation_id=row.correlation_id,
        published_at=aware(row.published_at) if row.published_at is not None else None,
        attempts=row.attempts,
        last_error=row.last_error,
        next_attempt_at=aware(row.next_attempt_at) if row.next_attempt_at is not None else None,
        dead_lettered=row.dead_lettered,
        headers=row.headers,
    )
