from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar
from uuid import UUID

NAMESPACE = "arcadia.reco.v1"


@dataclass(frozen=True, slots=True)
class EventStamp:
    """Identity and timestamp for one emitted event, supplied by the caller so the domain stays clock-free."""

    event_id: UUID
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class DomainEvent:
    """Base for every published event. `event_type` is derived from the class name rather than written per
    event: the two must never disagree, and a hand-copied string is how one gets a typo no checker sees — only
    a consumer that silently stops matching. The contract version lives in NAMESPACE, so a breaking change
    means a `v2` namespace rather than a field nobody routes on."""

    event_type: ClassVar[str] = f"{NAMESPACE}.DomainEvent"
    aggregate_type: ClassVar[str] = "Unknown"

    event_id: UUID
    occurred_at: datetime
    aggregate_id: UUID

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        cls.event_type = f"{NAMESPACE}.{cls.__name__}"
