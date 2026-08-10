from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from arcadia_recommendation.domain.shared.errors import InvariantViolation
from arcadia_recommendation.domain.shared.ids import GameId, RecommendationId, UserId


class RecommendationSource(StrEnum):
    """Which half of the hybrid produced a suggestion, as ER د-۱۲ records it. Carried out to the client
    because "because you liked racing games" and "because people who bought this bought that" are different
    claims, and a UI that cannot tell them apart cannot explain either."""

    CONTENT = "CONTENT"
    COLLAB = "COLLAB"
    HYBRID = "HYBRID"
    FALLBACK = "FALLBACK"


@dataclass(frozen=True, slots=True)
class Recommendation:
    """One suggested game with the score that earned its place.

    `rank` is stored rather than derived from a sort at read time. Scores are floats and ties are common at
    low signal counts, so two reads of the same stored set could otherwise order differently and a user
    would see the list shuffle on refresh.
    """

    id: RecommendationId
    user_id: UserId
    game_id: GameId
    score: float
    source: RecommendationSource
    rank: int
    generated_at: datetime
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.rank < 1:
            raise InvariantViolation("rank is one-based")
        if self.score < 0.0:
            raise InvariantViolation("a recommendation with a negative score should not have been kept")
