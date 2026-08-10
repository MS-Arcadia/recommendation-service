from datetime import datetime
from typing import Protocol
from uuid import UUID

from arcadia_recommendation.domain.shared.events import EventStamp


class ClockPort(Protocol):
    """Injected rather than called directly so every use-case test is deterministic without monkeypatching."""

    def now(self) -> datetime: ...


class IdGeneratorPort(Protocol):
    """Injected for the same reason as ClockPort: predictable identities in tests."""

    def new_uuid(self) -> UUID: ...


class EventStampFactory:
    """Composes the clock and the id generator into the (event_id, occurred_at) pair every domain behaviour
    needs, so use cases do not repeat that pairing at each call site."""

    def __init__(self, clock: ClockPort, ids: IdGeneratorPort) -> None:
        self._clock = clock
        self._ids = ids

    def next(self) -> EventStamp:
        return EventStamp(event_id=self._ids.new_uuid(), occurred_at=self._clock.now())

    def now(self) -> datetime:
        return self._clock.now()

    def new_uuid(self) -> UUID:
        return self._ids.new_uuid()
