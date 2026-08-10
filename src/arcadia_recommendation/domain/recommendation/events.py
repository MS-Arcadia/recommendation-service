from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar
from uuid import UUID

from arcadia_recommendation.domain.recommendation.recommendation import Recommendation
from arcadia_recommendation.domain.shared.events import DomainEvent, EventStamp
from arcadia_recommendation.domain.shared.ids import UserId

AGGREGATE_TYPE = "recommendation-set"


@dataclass(frozen=True, slots=True)
class RecommendationGenerated(DomainEvent):
    """Published on `reco-events` whenever a user's list is regenerated (architecture §4, ب-۹).

    The whole list travels in the event rather than a "go and fetch it" notification. Profile is the
    consumer, and a notification would mean every regeneration for every user turning into a call back into
    this service at exactly the moment the batch has it under most load.
    """

    aggregate_type: ClassVar[str] = AGGREGATE_TYPE

    user_id: UserId
    generated_at: datetime
    items: tuple[dict[str, object], ...]

    @classmethod
    def of(
        cls, user_id: UserId, recommendations: Sequence[Recommendation], stamp: EventStamp
    ) -> RecommendationGenerated:
        return cls(
            event_id=stamp.event_id,
            occurred_at=stamp.occurred_at,
            aggregate_id=user_id.value,
            user_id=user_id,
            generated_at=stamp.occurred_at,
            items=tuple(
                {
                    "game_id": str(item.game_id),
                    "score": round(item.score, 6),
                    "source": str(item.source),
                    "rank": item.rank,
                }
                for item in recommendations
            ),
        )

    @property
    def partition_id(self) -> UUID:
        return self.user_id.value
