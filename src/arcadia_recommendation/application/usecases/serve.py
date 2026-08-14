from collections.abc import Sequence

from arcadia_recommendation.application.dto.recommendation_dto import (
    RecommendationItem,
    RecommendationsResponse,
    SimilarGame,
    SimilarGamesResponse,
    items_of,
)
from arcadia_recommendation.application.ports.outbound.repositories import UnitOfWork, UnitOfWorkFactory
from arcadia_recommendation.domain.catalog.game_profile import GameProfile
from arcadia_recommendation.domain.policy.scoring import TopSellersProvider
from arcadia_recommendation.domain.preference.profile import UserPreference
from arcadia_recommendation.domain.recommendation.recommendation import (
    Recommendation,
    RecommendationSource,
)
from arcadia_recommendation.domain.shared import limits
from arcadia_recommendation.domain.shared.errors import NotFound
from arcadia_recommendation.domain.shared.ids import GameId, UserId


class ServeRecommendationsUseCase:
    """The online read path of ب-۹.

    Reads what the batch already decided and never ranks. If there is nothing stored — a new user, or a
    sweep that has not run yet — it answers with top sellers rather than an empty list, and says so in
    `source`. That distinction is the whole of the bulkhead here: this endpoint has no failure mode that
    returns nothing, so a storefront can render the section unconditionally.
    """

    def __init__(self, uow_factory: UnitOfWorkFactory, fallback: TopSellersProvider) -> None:
        self._uow_factory = uow_factory
        self._fallback = fallback

    async def execute(
        self, user_id: UserId, limit: int = limits.DEFAULT_RECOMMENDATION_COUNT
    ) -> RecommendationsResponse:
        async with self._uow_factory() as uow:
            stored = list(await uow.recommendations.for_user(user_id, limit))
            if stored:
                games = await uow.games.by_ids([item.game_id for item in stored])
                return RecommendationsResponse(
                    user_id=str(user_id),
                    source=_dominant_source(stored),
                    generated_at=stored[0].generated_at,
                    items=items_of(stored, games),
                )
            return await self._fallback_response(uow, user_id, limit)

    async def _fallback_response(
        self, uow: UnitOfWork, user_id: UserId, limit: int
    ) -> RecommendationsResponse:
        preference = await uow.preferences.get(user_id) or UserPreference.blank(user_id)
        candidates = await uow.games.recommendable(max(limit * 4, limit))
        top = self._fallback.rank(candidates, preference, limit)
        return RecommendationsResponse(
            user_id=str(user_id),
            source=RecommendationSource.FALLBACK,
            generated_at=None,
            items=[
                RecommendationItem(
                    game_id=str(game.game_id),
                    title=game.title,
                    genres=list(game.genres),
                    # Popularity is not a similarity, so it is reported as a share of the most-bought game in
                    # the set rather than dressed up as one. A client comparing scores across sources would
                    # be comparing different things either way, which is what `source` is there to say.
                    score=_popularity_share(game, top),
                    source=RecommendationSource.FALLBACK,
                    rank=rank,
                    reasons=[],
                )
                for rank, game in enumerate(top, start=1)
            ],
        )


class ListSimilarGamesUseCase:
    """Content-based neighbours of one game, with no user involved.

    Serves the "more like this" rail on a game page, and it is the only read here that ranks at request
    time — there is no per-user state to precompute against. The two spaces answer it differently because
    their vectors cost different amounts to move. Sparse features are a few dozen named weights, so a
    bounded catalogue scan and cosine in Python is arithmetic rather than a query. Dense vectors are a
    thousand floats each, and doing the same to them means deserialising five hundred rows to keep ten —
    so Postgres orders by distance and returns the ten.

    `shared_features` is populated only on the sparse path. Named dimensions can be intersected and
    anonymous ones cannot, so a dense answer says how alike two games are without claiming to say why.
    """

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        catalogue_limit: int = 500,
        dense_space: bool = False,
    ) -> None:
        self._uow_factory = uow_factory
        self._catalogue_limit = catalogue_limit
        self._dense_space = dense_space

    async def execute(
        self, game_id: GameId, limit: int = limits.DEFAULT_RECOMMENDATION_COUNT
    ) -> SimilarGamesResponse:
        async with self._uow_factory() as uow:
            subject = await uow.games.get(game_id)
            if subject is None:
                raise NotFound(f"game {game_id} is not known to this service")
            if self._dense_space:
                neighbours = await uow.games.nearest_to(subject, limit)
                return SimilarGamesResponse(
                    game_id=str(game_id),
                    items=[
                        SimilarGame(
                            game_id=str(candidate.game_id),
                            title=candidate.title,
                            genres=list(candidate.genres),
                            similarity=round(subject.dense.cosine(candidate.dense), 6),
                            shared_features=[],
                        )
                        for candidate in neighbours
                    ],
                )
            candidates = await uow.games.recommendable(self._catalogue_limit)

        scored = [
            (candidate, subject.embedding.cosine(candidate.embedding))
            for candidate in candidates
            if candidate.game_id != game_id
        ]
        ranked = sorted(
            (entry for entry in scored if entry[1] > 0.0),
            key=lambda entry: (-entry[1], -entry[0].purchase_count, str(entry[0].game_id)),
        )[:limit]
        return SimilarGamesResponse(
            game_id=str(game_id),
            items=[
                SimilarGame(
                    game_id=str(candidate.game_id),
                    title=candidate.title,
                    genres=list(candidate.genres),
                    similarity=round(similarity, 6),
                    shared_features=list(subject.embedding.overlap(candidate.embedding)),
                )
                for candidate, similarity in ranked
            ],
        )


def _dominant_source(stored: Sequence[Recommendation]) -> RecommendationSource:
    """What the list as a whole should be labelled.

    A list built by more than one method *is* a hybrid one, so a mixture reports HYBRID rather than
    whichever method happens to rank highest in some precedence order. An earlier version did exactly that
    and labelled a list COLLAB whose top three items were all content matches — the envelope was describing
    the rarest thing in the list instead of the list.
    """
    sources = {item.source for item in stored}
    if not sources:
        return RecommendationSource.FALLBACK
    if len(sources) > 1:
        return RecommendationSource.HYBRID
    return sources.pop()


def _popularity_share(game: GameProfile, ranked: list[GameProfile]) -> float:
    strongest = max((candidate.purchase_count for candidate in ranked), default=0)
    if strongest <= 0:
        return 0.0
    return round(game.purchase_count / strongest, 6)
