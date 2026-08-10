from collections.abc import Sequence
from datetime import datetime

from pydantic import BaseModel, Field

from arcadia_recommendation.domain.catalog.game_profile import GameProfile
from arcadia_recommendation.domain.recommendation.recommendation import (
    Recommendation,
    RecommendationSource,
)


class RecommendationItem(BaseModel):
    """One suggestion as it goes out on the wire.

    `title` and `genres` are denormalised into the response so a storefront can render the section from one
    call. Recommendation already holds them for ranking, and making the client fan out to Catalog for ten
    games would undo the point of a read-optimised service.
    """

    game_id: str
    title: str
    genres: list[str] = Field(default_factory=list)
    score: float
    source: RecommendationSource
    rank: int
    reasons: list[str] = Field(default_factory=list)

    @classmethod
    def of(cls, recommendation: Recommendation, game: GameProfile | None) -> RecommendationItem:
        return cls(
            game_id=str(recommendation.game_id),
            title=game.title if game is not None else "",
            genres=list(game.genres) if game is not None else [],
            score=round(recommendation.score, 6),
            source=recommendation.source,
            rank=recommendation.rank,
            reasons=list(recommendation.reasons),
        )


class RecommendationsResponse(BaseModel):
    """`source` at the envelope level answers the question a client actually has: was this personalised at
    all? A page that got FALLBACK should say "popular right now" rather than "picked for you"."""

    user_id: str
    source: RecommendationSource
    generated_at: datetime | None = None
    items: list[RecommendationItem] = Field(default_factory=list)

    @property
    def is_personalised(self) -> bool:
        return self.source is not RecommendationSource.FALLBACK


class SimilarGame(BaseModel):
    game_id: str
    title: str
    genres: list[str] = Field(default_factory=list)
    similarity: float
    shared_features: list[str] = Field(default_factory=list)


class SimilarGamesResponse(BaseModel):
    game_id: str
    items: list[SimilarGame] = Field(default_factory=list)


class RefreshReport(BaseModel):
    """What an operator gets back from a forced batch run: enough to tell "it did nothing" from "there was
    nothing to do"."""

    users_processed: int
    recommendations_written: int
    generated_at: datetime


def items_of(
    recommendations: Sequence[Recommendation], games: Sequence[GameProfile]
) -> list[RecommendationItem]:
    catalogue = {game.game_id: game for game in games}
    return [RecommendationItem.of(item, catalogue.get(item.game_id)) for item in recommendations]
