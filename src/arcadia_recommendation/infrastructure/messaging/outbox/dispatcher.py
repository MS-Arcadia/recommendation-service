import asyncio
import contextlib
import random
from datetime import UTC, datetime, timedelta

from arcadia_recommendation.infrastructure.messaging.bus.publisher import EventPublisher
from arcadia_recommendation.infrastructure.messaging.outbox.record import OutboxRecord
from arcadia_recommendation.infrastructure.messaging.outbox.store import OutboxStore

MAX_ATTEMPTS = 5
BASE_BACKOFF_SECONDS = 0.5
DEFAULT_BATCH_SIZE = 50


class OutboxDispatcher:
    """Drains committed outbox records to the publisher, at-least-once. A publish failure is retried with
    exponential backoff and jitter; past MAX_ATTEMPTS the record is dead-lettered rather than retried forever,
    because an unbounded retry on a poison message stalls every event behind it.

    Nothing here fails a write: if the publisher is down the records simply stay pending and drain on
    recovery, which is the entire payoff of the outbox pattern."""

    def __init__(
        self,
        store: OutboxStore,
        publisher: EventPublisher,
        poll_interval: timedelta = timedelta(milliseconds=500),
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> None:
        self._store = store
        self._publisher = publisher
        self._poll_interval = poll_interval
        self._batch_size = batch_size
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def pending_count(self) -> int:
        return self._store.pending_count

    @property
    def dlq_depth(self) -> int:
        return self._store.dlq_depth

    async def start(self) -> None:
        if self.is_running:
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def drain_once(self) -> int:
        published = 0
        now = datetime.now(UTC)
        for record in await self._store.due(now, self._batch_size):
            if await self._publish(record, now):
                published += 1
        return published

    async def _publish(self, record: OutboxRecord, now: datetime) -> bool:
        record.attempts += 1
        try:
            await self._publisher.publish(record)
        except Exception as exc:
            record.last_error = f"{type(exc).__name__}: {exc}"
            if record.attempts >= MAX_ATTEMPTS:
                record.dead_lettered = True
            else:
                backoff = BASE_BACKOFF_SECONDS * (2 ** (record.attempts - 1))
                jitter = backoff * random.random() * 0.1
                record.next_attempt_at = now + timedelta(seconds=backoff + jitter)
            await self._store.record_failure(record)
            return False
        record.published_at = now
        record.last_error = None
        await self._store.record_published(record)
        return True

    async def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                await self.drain_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            await asyncio.sleep(self._poll_interval.total_seconds())
