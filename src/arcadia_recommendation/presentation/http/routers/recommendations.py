from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from fastapi.encoders import jsonable_encoder

from arcadia_recommendation.application.dto.recommendation_dto import (
    RecommendationsResponse,
    SimilarGamesResponse,
)
from arcadia_recommendation.application.errors import AuthorizationError
from arcadia_recommendation.domain.shared import limits
from arcadia_recommendation.domain.shared.ids import GameId, UserId
from arcadia_recommendation.presentation.http.deps.auth import ActorDep
from arcadia_recommendation.presentation.http.deps.container import ResponseCacheDep, UseCasesDep

router = APIRouter(tags=["Recommendations"])

LimitDep = Annotated[
    int, Query(ge=1, le=limits.MAX_RECOMMENDATION_COUNT, description="How many suggestions to return")
]


@router.get("/recommendations", response_model=RecommendationsResponse)
async def my_recommendations(
    actor: ActorDep,
    use_cases: UseCasesDep,
    cache: ResponseCacheDep,
    limit: LimitDep = limits.DEFAULT_RECOMMENDATION_COUNT,
) -> RecommendationsResponse:
    """The caller's own suggestions.

    Cached for seconds rather than not at all: the underlying list only changes when the batch runs, so
    within one page load a storefront rendering this section twice should not pay for it twice.
    """

    async def compute() -> object:
        return jsonable_encoder(await use_cases.serve.execute(actor.user_id, limit))

    payload = await cache.get_or_set(f"user:{actor.user_id}:reco:{limit}", compute)
    return RecommendationsResponse.model_validate(payload)


@router.get("/users/{user_id}/recommendations", response_model=RecommendationsResponse)
async def recommendations_for(
    user_id: UUID,
    actor: ActorDep,
    use_cases: UseCasesDep,
    limit: LimitDep = limits.DEFAULT_RECOMMENDATION_COUNT,
) -> RecommendationsResponse:
    """Another user's suggestions, for Support and for the caller themselves.

    Uncached, unlike the route above: this one is answered for whoever asks, and a cache keyed by subject
    rather than by caller is how one user's list ends up served to another.
    """
    target = UserId(user_id)
    if target != actor.user_id and not actor.is_moderator:
        raise AuthorizationError("only Support or the user themselves may read these recommendations")
    return await use_cases.serve.execute(target, limit)


@router.get("/games/{game_id}/similar", response_model=SimilarGamesResponse)
async def similar_games(
    game_id: UUID,
    use_cases: UseCasesDep,
    cache: ResponseCacheDep,
    limit: LimitDep = limits.DEFAULT_RECOMMENDATION_COUNT,
) -> SimilarGamesResponse:
    """Content-based neighbours of one game — the "more like this" rail, and the only public route here.

    Public because the answer does not depend on who is asking: it is a property of the catalogue, which is
    itself public, so requiring a token would buy nothing and stop a logged-out visitor seeing the rail.
    """

    async def compute() -> object:
        return jsonable_encoder(await use_cases.similar_games.execute(GameId(game_id), limit))

    payload = await cache.get_or_set(f"game:{game_id}:similar:{limit}", compute)
    return SimilarGamesResponse.model_validate(payload)
