from collections.abc import Callable, Mapping, Sequence
from types import TracebackType
from typing import Protocol, Self

from arcadia_recommendation.application.ports.outbound.messaging import OutboxPort, ProcessedEventStore
from arcadia_recommendation.domain.catalog.game_profile import GameProfile
from arcadia_recommendation.domain.preference.profile import UserPreference
from arcadia_recommendation.domain.recommendation.recommendation import Recommendation
from arcadia_recommendation.domain.shared.ids import GameId, UserId


class GameProfileRepository(Protocol):
    """The read-model of Catalog's games, as far as ranking needs it."""

    async def get(self, game_id: GameId) -> GameProfile | None: ...

    async def upsert(self, game: GameProfile) -> None: ...

    async def recommendable(self, limit: int) -> Sequence[GameProfile]: ...

    async def by_ids(self, game_ids: Sequence[GameId]) -> Sequence[GameProfile]: ...

    async def count_recommendable(self) -> int: ...

    async def needing_embedding(self, limit: int) -> Sequence[GameProfile]: ...

    async def nearest_to(self, game: GameProfile, limit: int) -> Sequence[GameProfile]: ...


class UserPreferenceRepository(Protocol):
    """One row per user this service has ever heard about."""

    async def get(self, user_id: UserId) -> UserPreference | None: ...

    async def upsert(self, preference: UserPreference) -> None: ...

    async def with_signals(self, limit: int) -> Sequence[UserId]: ...


class OwnershipRepository(Protocol):
    """The collaborative signal, kept as (user, game) pairs rather than folded into the preference vector.

    `co_purchases` is the item-item query, and it is a port method rather than something the domain computes
    because it is a self-join whose whole point is that the database does it: answering it in Python means
    loading every ownership row on the platform to rank ten games.

    `counted` records whether a purchase also reached the owner's taste vector. It exists because the two
    topics are independent: `PurchaseCompleted` can arrive before the `GamePublished` that describes what was
    bought, and on a cold start replaying history it usually does. Without this flag those purchases feed the
    collaborative half and are silently lost to the content half for ever.
    """

    async def record(self, user_id: UserId, game_id: GameId, *, counted: bool) -> None: ...

    async def uncounted_owners(self, game_id: GameId, limit: int) -> Sequence[UserId]: ...

    async def mark_counted(self, game_id: GameId, user_ids: Sequence[UserId]) -> None: ...

    async def co_purchases(
        self, user_id: UserId, owned: Sequence[GameId], limit: int
    ) -> Mapping[GameId, int]: ...


class RecommendationRepository(Protocol):
    """The generated lists, one set per user, replaced wholesale on each run."""

    async def replace_for(self, user_id: UserId, recommendations: Sequence[Recommendation]) -> None: ...

    async def for_user(self, user_id: UserId, limit: int) -> Sequence[Recommendation]: ...


class UnitOfWork(Protocol):
    """One transaction spanning every repository and the outbox, so an ingested signal and the event it
    produces commit together or not at all. The repositories are exposed read-only so an implementation may
    hold narrower concrete types than the ports declared here."""

    @property
    def games(self) -> GameProfileRepository: ...

    @property
    def preferences(self) -> UserPreferenceRepository: ...

    @property
    def ownerships(self) -> OwnershipRepository: ...

    @property
    def recommendations(self) -> RecommendationRepository: ...

    @property
    def outbox(self) -> OutboxPort: ...

    @property
    def processed_events(self) -> ProcessedEventStore: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


UnitOfWorkFactory = Callable[[], UnitOfWork]
