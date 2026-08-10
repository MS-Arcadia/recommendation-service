from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from arcadia_recommendation.domain.catalog.game_profile import GameProfile
from arcadia_recommendation.domain.preference.profile import UserPreference
from arcadia_recommendation.domain.recommendation.recommendation import Recommendation
from arcadia_recommendation.domain.shared.ids import GameId, UserId
from arcadia_recommendation.infrastructure.messaging.outbox.record import OutboxRecord


@dataclass
class MemoryStore:
    """The whole database, for a local run without Postgres.

    Aggregates are frozen dataclasses, so holding a reference is safe — there is no instance a caller can
    mutate behind the store's back, which is the usual reason an in-memory fake diverges from the real thing.
    """

    games: dict[GameId, GameProfile] = field(default_factory=dict)
    preferences: dict[UserId, UserPreference] = field(default_factory=dict)
    # game -> owner -> whether the purchase also reached that owner's taste vector.
    ownerships: defaultdict[GameId, dict[UserId, bool]] = field(default_factory=lambda: defaultdict(dict))
    recommendations: defaultdict[UserId, list[Recommendation]] = field(
        default_factory=lambda: defaultdict(list)
    )
    outbox: dict[UUID, OutboxRecord] = field(default_factory=dict)
    processed_events: set[UUID] = field(default_factory=set)

    def owners_of(self, game_id: GameId) -> set[UserId]:
        return set(self.ownerships.get(game_id, {}))

    def owned_by(self, user_id: UserId) -> set[GameId]:
        return {game_id for game_id, owners in self.ownerships.items() if user_id in owners}

    def now(self) -> datetime:
        return datetime.now(UTC)
