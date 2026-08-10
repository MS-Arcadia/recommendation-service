from contextvars import ContextVar
from uuid import uuid4

CORRELATION_ID_HEADER = "X-Correlation-Id"

_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)
_actor_id: ContextVar[str | None] = ContextVar("actor_id", default=None)


def new_correlation_id() -> str:
    return str(uuid4())


def bind_correlation_id(correlation_id: str) -> None:
    _correlation_id.set(correlation_id)


def current_correlation_id() -> str | None:
    return _correlation_id.get()


def bind_actor_id(actor_id: str | None) -> None:
    _actor_id.set(actor_id)


def current_actor_id() -> str | None:
    return _actor_id.get()
