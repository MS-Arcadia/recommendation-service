from collections.abc import Sequence
from types import TracebackType
from typing import Self
from uuid import UUID

from arcadia_recommendation.domain.catalog.game_profile import GameProfile
from arcadia_recommendation.domain.preference.profile import UserPreference
from arcadia_recommendation.domain.recommendation.recommendation import Recommendation
from arcadia_recommendation.domain.shared.events import DomainEvent
from arcadia_recommendation.domain.shared.ids import GameId, UserId
from arcadia_recommendation.infrastructure.messaging.outbox.record import OutboxRecord
from arcadia_recommendation.infrastructure.messaging.serialization import event_envelope, partition_key
from arcadia_recommendation.infrastructure.persistence.memory.repositories import (
    MemoryGameProfileRepository,
    MemoryOwnershipRepository,
    MemoryRecommendationRepository,
    MemoryUserPreferenceRepository,
)
from arcadia_recommendation.infrastructure.persistence.memory.store import MemoryStore


class MemoryUnitOfWork:
    """Transactional semantics without a transaction: every write is staged and applied to the store only on
    commit. Not decoration — the use cases below rely on a failed ingest leaving no partial state, and a fake
    that applied writes immediately would pass tests the real backend fails."""

    def __init__(self, store: MemoryStore, topic: str, correlation_id: str | None = None) -> None:
        self._store = store
        self._committed = False
        self._games: dict[GameId, GameProfile] = {}
        self._preferences: dict[UserId, UserPreference] = {}
        self._ownerships: list[tuple[UserId, GameId, bool]] = []
        self._recommendations: dict[UserId, list[Recommendation]] = {}
        self._events: list[OutboxRecord] = []
        self._processed: set[UUID] = set()

        self.games = MemoryGameProfileRepository(store, self._games)
        self.preferences = MemoryUserPreferenceRepository(store, self._preferences)
        self.ownerships = MemoryOwnershipRepository(store, self._ownerships)
        self.recommendations = MemoryRecommendationRepository(store, self._recommendations)
        self.outbox = MemoryOutbox(self._events, topic, correlation_id)
        self.processed_events = MemoryProcessedEventStore(store, self._processed)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if not self._committed:
            await self.rollback()

    async def commit(self) -> None:
        self._store.games.update(self._games)
        self._store.preferences.update(self._preferences)
        for user_id, game_id, counted in self._ownerships:
            # Raised, never lowered, matching the SQL upsert: a redelivered purchase must not un-count a
            # signal already folded into a taste vector that cannot be credited twice.
            owners = self._store.ownerships[game_id]
            owners[user_id] = owners.get(user_id, False) or counted
        for user_id, recommendations in self._recommendations.items():
            self._store.recommendations[user_id] = recommendations
        for record in self._events:
            self._store.outbox[record.id] = record
        self._store.processed_events |= self._processed
        self._committed = True

    async def rollback(self) -> None:
        self._games.clear()
        self._preferences.clear()
        self._ownerships.clear()
        self._recommendations.clear()
        self._events.clear()
        self._processed.clear()


class MemoryOutbox:
    def __init__(self, staged: list[OutboxRecord], topic: str, correlation_id: str | None) -> None:
        self._staged = staged
        self._topic = topic
        self._correlation_id = correlation_id

    async def enqueue(self, events: Sequence[DomainEvent]) -> None:
        for event in events:
            self._staged.append(
                OutboxRecord(
                    id=event.event_id,
                    aggregate_id=event.aggregate_id,
                    aggregate_type=event.aggregate_type,
                    event_type=event.event_type,
                    payload=event_envelope(event, self._correlation_id),
                    occurred_at=event.occurred_at,
                    topic=self._topic,
                    partition_key=partition_key(event),
                    correlation_id=self._correlation_id,
                )
            )


class MemoryProcessedEventStore:
    def __init__(self, store: MemoryStore, staged: set[UUID]) -> None:
        self._store = store
        self._staged = staged

    async def seen(self, event_id: UUID) -> bool:
        return event_id in self._store.processed_events or event_id in self._staged

    async def mark(self, event_id: UUID) -> None:
        self._staged.add(event_id)
