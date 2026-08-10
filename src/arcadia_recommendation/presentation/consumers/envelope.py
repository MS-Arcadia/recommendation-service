from datetime import datetime
from typing import Any
from uuid import UUID

from arcadia_recommendation.domain.shared.errors import InvariantViolation
from arcadia_recommendation.domain.shared.ids import GameId, UserId
from arcadia_recommendation.infrastructure.messaging.outbox.record import OutboxRecord


def payload_of(record: OutboxRecord) -> dict[str, Any]:
    """The domain fields of the platform envelope. Every `-events` topic on the platform nests them under
    `payload`, and a message that does not is one this service should ignore rather than guess at."""
    payload = record.payload.get("payload")
    return payload if isinstance(payload, dict) else {}


def game_id_of(payload: dict[str, Any], key: str = "game_id") -> GameId | None:
    raw = payload.get(key)
    if raw is None:
        return None
    try:
        return GameId.parse(str(raw))
    except InvariantViolation:
        return None


def user_id_of(payload: dict[str, Any], key: str) -> UserId | None:
    raw = payload.get(key)
    if raw is None:
        return None
    try:
        return UserId.parse(str(raw))
    except InvariantViolation:
        return None


def timestamp_of(raw: object) -> datetime | None:
    """Order publishes RFC 3339 with a `Z` so Go can unmarshal it; the Python services publish an offset.
    Both are accepted here, because this service consumes from both."""
    if raw is None:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def event_id_of(record: OutboxRecord) -> UUID:
    """What deduplication keys on. The envelope's own `event_id` where there is one, so a redelivery is
    recognised as the same event rather than as a new one that happens to say the same thing."""
    raw = record.payload.get("event_id")
    return UUID(str(raw)) if raw is not None else record.id
