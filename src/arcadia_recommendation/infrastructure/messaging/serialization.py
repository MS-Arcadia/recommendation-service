from dataclasses import fields
from datetime import datetime
from enum import Enum
from typing import Any, Final
from uuid import UUID

from arcadia_recommendation.domain.shared.events import DomainEvent

PRODUCER: Final = "recommendation-service"
TOPIC: Final = "reco-events"

SCHEMA_VERSION: Final = 1

_ENVELOPE_FIELDS: Final = frozenset({"event_id", "occurred_at", "aggregate_id"})


def _primitive(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return str(value)
    if isinstance(value, tuple | list):
        return [_primitive(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _primitive(item) for key, item in value.items()}
    if hasattr(value, "value") and isinstance(getattr(value, "value", None), UUID):
        return str(value)
    return value


def event_payload(event: DomainEvent) -> dict[str, Any]:
    """The domain fields only, under their Python names. `event_id`, `occurred_at` and `aggregate_id` are
    envelope concerns and are excluded: carrying them in both places is how a consumer ends up reading one
    while the producer sets the other."""
    return {
        field.name: _primitive(getattr(event, field.name))
        for field in fields(event)
        if field.name not in _ENVELOPE_FIELDS
    }


def event_envelope(event: DomainEvent, correlation_id: str | None) -> dict[str, Any]:
    """The platform's event envelope, identical in shape and key casing across all 14 services. Consumers
    validate the envelope before they look at anything else and route on `event_type`, so the domain fields
    live inside `payload` and never at the top level."""
    return {
        "event_id": str(event.event_id),
        "event_type": event.event_type,
        "schema_version": SCHEMA_VERSION,
        "occurred_at": event.occurred_at.isoformat(),
        "producer": PRODUCER,
        "aggregate_type": event.aggregate_type,
        "aggregate_id": str(event.aggregate_id),
        "correlation_id": correlation_id,
        "payload": event_payload(event),
    }


def partition_key(event: DomainEvent) -> str:
    """Everything this service publishes is about one user, and the aggregate id *is* the user id. Keying on
    it keeps successive regenerations of one user's list mutually ordered, so Profile's read-model cannot
    apply an older set over a newer one."""
    return str(event.aggregate_id)
