from collections import Counter
from collections.abc import Mapping, Sequence

from arcadia_recommendation.domain.catalog.game_profile import GameProfile
from arcadia_recommendation.domain.preference.profile import UserPreference
from arcadia_recommendation.domain.recommendation.recommendation import Recommendation
from arcadia_recommendation.domain.shared.ids import GameId, UserId
from arcadia_recommendation.infrastructure.persistence.memory.store import MemoryStore


class MemoryGameProfileRepository:
    """Writes are staged and applied on commit, mirroring the SQL unit of work: a use case that raises after
    a write must leave nothing behind, and a fake that wrote through would hide exactly that bug."""

    def __init__(self, store: MemoryStore, staged: dict[GameId, GameProfile]) -> None:
        self._store = store
        self._staged = staged

    async def get(self, game_id: GameId) -> GameProfile | None:
        return self._staged.get(game_id) or self._store.games.get(game_id)

    async def upsert(self, game: GameProfile) -> None:
        self._staged[game.game_id] = game

    async def recommendable(self, limit: int) -> Sequence[GameProfile]:
        merged = {**self._store.games, **self._staged}
        eligible = [game for game in merged.values() if game.is_published]
        eligible.sort(key=lambda game: (-game.purchase_count, str(game.game_id)))
        return eligible[:limit]

    async def by_ids(self, game_ids: Sequence[GameId]) -> Sequence[GameProfile]:
        merged = {**self._store.games, **self._staged}
        return [merged[game_id] for game_id in game_ids if game_id in merged]

    async def count_recommendable(self) -> int:
        merged = {**self._store.games, **self._staged}
        return sum(1 for game in merged.values() if game.is_published)


class MemoryUserPreferenceRepository:
    def __init__(self, store: MemoryStore, staged: dict[UserId, UserPreference]) -> None:
        self._store = store
        self._staged = staged

    async def get(self, user_id: UserId) -> UserPreference | None:
        return self._staged.get(user_id) or self._store.preferences.get(user_id)

    async def upsert(self, preference: UserPreference) -> None:
        self._staged[preference.user_id] = preference

    async def with_signals(self, limit: int) -> Sequence[UserId]:
        merged = {**self._store.preferences, **self._staged}
        active = [item for item in merged.values() if item.signal_count > 0]
        active.sort(key=lambda item: (item.updated_at is None, str(item.user_id)))
        return [item.user_id for item in active[:limit]]


class MemoryOwnershipRepository:
    def __init__(self, store: MemoryStore, staged: list[tuple[UserId, GameId, bool]]) -> None:
        self._store = store
        self._staged = staged

    async def record(self, user_id: UserId, game_id: GameId, *, counted: bool) -> None:
        self._staged.append((user_id, game_id, counted))

    async def uncounted_owners(self, game_id: GameId, limit: int) -> Sequence[UserId]:
        pending = [
            user_id for user_id, counted in self._store.ownerships.get(game_id, {}).items() if not counted
        ]
        staged = [
            user_id
            for user_id, staged_game, counted in self._staged
            if staged_game == game_id and not counted and user_id not in pending
        ]
        return (pending + staged)[:limit]

    async def mark_counted(self, game_id: GameId, user_ids: Sequence[UserId]) -> None:
        for user_id in user_ids:
            self._staged.append((user_id, game_id, True))

    async def co_purchases(
        self, user_id: UserId, owned: Sequence[GameId], limit: int
    ) -> Mapping[GameId, int]:
        if not owned:
            return {}
        owned_set = set(owned)
        neighbours: set[UserId] = set()
        for game_id in owned_set:
            neighbours |= self._store.owners_of(game_id)
        neighbours.discard(user_id)

        counts: Counter[GameId] = Counter()
        for neighbour in neighbours:
            for game_id in self._store.owned_by(neighbour) - owned_set:
                counts[game_id] += 1
        return dict(counts.most_common(limit))


class MemoryRecommendationRepository:
    def __init__(self, store: MemoryStore, staged: dict[UserId, list[Recommendation]]) -> None:
        self._store = store
        self._staged = staged

    async def replace_for(self, user_id: UserId, recommendations: Sequence[Recommendation]) -> None:
        self._staged[user_id] = list(recommendations)

    async def for_user(self, user_id: UserId, limit: int) -> Sequence[Recommendation]:
        stored = self._staged.get(user_id, self._store.recommendations.get(user_id, []))
        return sorted(stored, key=lambda item: item.rank)[:limit]
