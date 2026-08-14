from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime

from arcadia_recommendation.domain.catalog.game_profile import GameProfile
from arcadia_recommendation.domain.policy.embedding import DenseEmbedding, Embedding
from arcadia_recommendation.domain.preference.signal import SignalKind, SignalRecord, weight_of
from arcadia_recommendation.domain.shared import limits
from arcadia_recommendation.domain.shared.ids import GameId, UserId


@dataclass(frozen=True, slots=True)
class UserPreference:
    """What this service believes one user likes, as a vector over the same feature space games live in.

    The `owned` set is held here rather than read from Catalog on every request for two reasons: a
    recommendation must never suggest a game the user already has, and that exclusion is on the hot read
    path, where a synchronous call into another service would be both the slowest step and the one that
    turns a Catalog outage into a Recommendation outage.

    `taste` and `taste_dense` are the same belief in two spaces. Both are folded on ingest, but only the
    dense one can be wrong there — a game bought before its embedding was computed contributes nothing to
    it. `history` is what repairs that: `rebuilt_in` recomputes the dense vector from the actions
    themselves, which the sweep does on every pass.
    """

    user_id: UserId
    taste: Embedding = field(default_factory=Embedding.empty)
    taste_dense: DenseEmbedding = field(default_factory=DenseEmbedding.empty)
    owned: frozenset[GameId] = frozenset()
    history: tuple[SignalRecord, ...] = ()
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
        weight = weight_of(kind)
        record = SignalRecord(game_id=game.game_id, weight=weight)
        return replace(
            self,
            taste=self.taste.plus(game.embedding, scale=weight),
            taste_dense=self.taste_dense.plus(game.dense, scale=weight),
            owned=self.owned | {game.game_id} if kind is SignalKind.PURCHASE else self.owned,
            history=(*self.history, record)[-limits.MAX_SIGNAL_HISTORY :],
            signal_count=self.signal_count + 1,
            updated_at=at,
        )

    def rebuilt_in(self, space: Mapping[GameId, DenseEmbedding]) -> UserPreference:
        """The dense taste vector, recomputed from remembered actions against the vectors available now.

        Folding on ingest is not enough on its own, because the two arrive independently: an embedding is a
        call to a third party and a purchase is not going to wait for it. Recomputing from history is what
        turns that race into a delay — the signal is applied on the next sweep instead of being lost. Games
        the caller did not supply a vector for are skipped, so a still-unembedded game contributes nothing
        rather than distorting the sum.
        """
        rebuilt = DenseEmbedding.empty()
        for record in self.history:
            vector = space.get(record.game_id)
            if vector is not None and not vector.is_empty:
                rebuilt = rebuilt.plus(vector, scale=record.weight)
        return replace(self, taste_dense=rebuilt)

    def owns(self, game_id: GameId) -> bool:
        return game_id in self.owned

    @property
    def remembered_games(self) -> tuple[GameId, ...]:
        """The games behind the history, most recent first and without repeats — what a caller needs to
        fetch to rebuild the dense vector, or to tell a model what this user has actually played."""
        seen: dict[GameId, None] = {}
        for record in reversed(self.history):
            seen.setdefault(record.game_id, None)
        return tuple(seen)
