from dataclasses import dataclass, field, replace
from datetime import datetime

from arcadia_recommendation.domain.catalog.game_profile import GameProfile
from arcadia_recommendation.domain.policy.embedding import Embedding
from arcadia_recommendation.domain.preference.signal import SignalKind, weight_of
from arcadia_recommendation.domain.shared.ids import GameId, UserId


@dataclass(frozen=True, slots=True)
class UserPreference:
    """What this service believes one user likes, as a vector over the same feature space games live in.

    The `owned` set is held here rather than read from Catalog on every request for two reasons: a
    recommendation must never suggest a game the user already has, and that exclusion is on the hot read
    path, where a synchronous call into another service would be both the slowest step and the one that
    turns a Catalog outage into a Recommendation outage.
    """

    user_id: UserId
    taste: Embedding = field(default_factory=Embedding.empty)
    owned: frozenset[GameId] = frozenset()
    signal_count: int = 0
    updated_at: datetime | None = None

    @classmethod
    def blank(cls, user_id: UserId) -> UserPreference:
        return cls(user_id=user_id)

    @property
    def is_cold(self) -> bool:
        """A user this service has learned nothing about. Serving them a personalised list would mean
        ranking on an empty vector, which orders by nothing at all — the fallback is the honest answer."""
        return self.signal_count == 0 or self.taste.is_empty

    def observe(self, game: GameProfile, kind: SignalKind, at: datetime) -> UserPreference:
        """Folds one action into the profile. Idempotency is the caller's problem, not this method's: whether
        a redelivered PurchaseCompleted should count twice is a question about the event log, and answering
        it here would mean the domain holding a set of every event id it has ever seen."""
        taste = self.taste.plus(game.embedding, scale=weight_of(kind))
        owned = self.owned | {game.game_id} if kind is SignalKind.PURCHASE else self.owned
        return replace(
            self,
            taste=taste,
            owned=owned,
            signal_count=self.signal_count + 1,
            updated_at=at,
        )

    def owns(self, game_id: GameId) -> bool:
        return game_id in self.owned
