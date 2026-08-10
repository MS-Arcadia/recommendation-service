from arcadia_recommendation.application.usecases.ingest import (
    HandleGamePublishedUseCase,
    HandleGameWithdrawnUseCase,
)
from arcadia_recommendation.infrastructure.messaging.outbox.record import OutboxRecord
from arcadia_recommendation.infrastructure.observability.logging import get_logger
from arcadia_recommendation.presentation.consumers.envelope import (
    event_id_of,
    game_id_of,
    payload_of,
    timestamp_of,
    user_id_of,
)

TOPIC = "game-events"
GAME_PUBLISHED = "arcadia.catalog.v1.GamePublished"
GAME_WITHDRAWN = "arcadia.catalog.v1.GameWithdrawn"

_logger = get_logger(__name__)


class GamePublishedHandler:
    """Anti-corruption boundary for `game-events`. Catalog's payload shape stops at this class.

    Catalog's `_game_payload` carries `genres` and `tags` as lists of strings; both are read defensively
    because this service is a consumer of a contract it does not own, and a malformed one should cost a
    warning rather than a dead-lettered partition.
    """

    def __init__(self, handle: HandleGamePublishedUseCase) -> None:
        self._handle = handle

    async def __call__(self, record: OutboxRecord) -> None:
        payload = payload_of(record)
        game_id = game_id_of(payload)
        developer_id = user_id_of(payload, "developer_id")
        if game_id is None or developer_id is None:
            _logger.warning("game_published_ignored", reason="missing or invalid ids")
            return
        title = str(payload.get("title") or "").strip()
        if not title:
            _logger.warning("game_published_ignored", reason="missing title", game_id=str(game_id))
            return
        await self._handle.execute(
            event_id_of(record),
            game_id=game_id,
            developer_id=developer_id,
            title=title[:200],
            genres=_strings(payload.get("genres")),
            tags=_strings(payload.get("tags")),
            published_at=timestamp_of(payload.get("published_at")),
        )


class GameWithdrawnHandler:
    """A withdrawn game leaves the candidate set but keeps its co-purchase history."""

    def __init__(self, handle: HandleGameWithdrawnUseCase) -> None:
        self._handle = handle

    async def __call__(self, record: OutboxRecord) -> None:
        game_id = game_id_of(payload_of(record))
        if game_id is None:
            _logger.warning("game_withdrawn_ignored", reason="missing or invalid game_id")
            return
        await self._handle.execute(event_id_of(record), game_id)


def _strings(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    return tuple(str(item).strip() for item in raw if str(item).strip())
