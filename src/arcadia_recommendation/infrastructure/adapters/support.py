from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4


class SystemClock:
    """The production clock. Always UTC — a naive datetime crossing a service boundary is a latent bug."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class UuidGenerator:
    """The production id source."""

    def new_uuid(self) -> UUID:
        return uuid4()


class FixedClock:
    """A controllable clock for local runs and fixtures, so seeded data has stable timestamps."""

    def __init__(self, start: datetime, step: timedelta = timedelta(seconds=1)) -> None:
        self._instant = start
        self._step = step

    def now(self) -> datetime:
        self._instant += self._step
        return self._instant
