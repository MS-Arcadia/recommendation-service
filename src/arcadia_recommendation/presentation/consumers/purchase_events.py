from arcadia_recommendation.application.usecases.ingest import HandlePurchaseCompletedUseCase
from arcadia_recommendation.infrastructure.messaging.outbox.record import OutboxRecord
from arcadia_recommendation.infrastructure.observability.logging import get_logger
from arcadia_recommendation.presentation.consumers.envelope import (
    event_id_of,
    game_id_of,
    payload_of,
    timestamp_of,
    user_id_of,
)

TOPIC = "purchase-events"
PURCHASE_COMPLETED = "arcadia.order.v1.PurchaseCompleted"

_logger = get_logger(__name__)


class PurchaseCompletedHandler:
    """Anti-corruption boundary for `purchase-events`.

    Order's payload carries both `buyer_id` and `recipient_id`, and for an ordinary purchase they are the
    same user. `recipient_id` is the one read, because on a gift it is the recipient who ends up owning the
    game — see the use case for why crediting the payer would be actively wrong. It falls back to `buyer_id`
    only if the field is absent, which would mean a contract older than gifting.
    """

    def __init__(self, handle: HandlePurchaseCompletedUseCase) -> None:
        self._handle = handle

    async def __call__(self, record: OutboxRecord) -> None:
        payload = payload_of(record)
        recipient_id = user_id_of(payload, "recipient_id") or user_id_of(payload, "buyer_id")
        game_id = game_id_of(payload)
        if recipient_id is None or game_id is None:
            _logger.warning("purchase_completed_ignored", reason="missing or invalid ids")
            return
        await self._handle.execute(
            event_id_of(record),
            recipient_id=recipient_id,
            game_id=game_id,
            occurred_at=timestamp_of(payload.get("completed_at")) or timestamp_of(payload.get("created_at")),
        )
