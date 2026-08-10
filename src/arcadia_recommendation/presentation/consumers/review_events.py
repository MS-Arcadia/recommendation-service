from arcadia_recommendation.application.usecases.ingest import HandleReviewPostedUseCase
from arcadia_recommendation.infrastructure.messaging.outbox.record import OutboxRecord
from arcadia_recommendation.infrastructure.observability.logging import get_logger
from arcadia_recommendation.presentation.consumers.envelope import (
    event_id_of,
    game_id_of,
    payload_of,
    timestamp_of,
    user_id_of,
)

TOPIC = "review-events"
REVIEW_POSTED = "arcadia.review.v1.ReviewPosted"

# Review's own Sentiment enum. Mirrored rather than imported: this service must not depend on another
# service's code, and the wire contract is the string.
LIKE = "LIKE"

_logger = get_logger(__name__)


class ReviewPostedHandler:
    """Anti-corruption boundary for `review-events`.

    An unrecognised sentiment is treated as a dislike rather than dropped: Review only ever sends LIKE or
    DISLIKE, so anything else means the contract moved, and the conservative reading of an unknown opinion is
    the one that does not amplify a genre on a guess.
    """

    def __init__(self, handle: HandleReviewPostedUseCase) -> None:
        self._handle = handle

    async def __call__(self, record: OutboxRecord) -> None:
        payload = payload_of(record)
        author_id = user_id_of(payload, "author_id")
        game_id = game_id_of(payload)
        if author_id is None or game_id is None:
            _logger.warning("review_posted_ignored", reason="missing or invalid ids")
            return
        await self._handle.execute(
            event_id_of(record),
            author_id=author_id,
            game_id=game_id,
            liked=str(payload.get("sentiment", "")).upper() == LIKE,
            occurred_at=timestamp_of(record.payload.get("occurred_at")),
        )
