"""The correlation id survives an unhandled exception.

`register_exception_handlers`'s catch-all `Exception` handler logs "unhandled_error", and
Starlette pulls any handler registered for `Exception` (or 500) out of the normal chain into
`ServerErrorMiddleware`, which sits *outside every user middleware*. `CorrelationIdMiddleware`
used to sit between `RequestLogMiddleware` and `PrometheusMetricsMiddleware` in the
registration order — and Starlette's `add_middleware()` prepends, so the middleware added
*last* ends up *outer*most. That put metrics outside correlation.

`BaseHTTPMiddleware` (what all three are) runs everything inside it in a separate `anyio`
task, and on an exception re-raises it back in the *parent* task, not the one where it
originated. `bind_correlation_id()` ran inside `CorrelationIdMiddleware`'s own task — a
child task spawned by the (then-outer) metrics middleware's `call_next()` — so it was
invisible once the exception hopped back out into metrics' own task, and stayed invisible
all the way to `ServerErrorMiddleware`.

`_add_correlation` (the structlog processor that stamps `correlation_id` onto an event)
reads the ContextVar at *log* time, synchronous with whichever task is current — so this
uses `structlog.testing.capture_logs` with that exact processor included, which runs the
real injection logic against whatever the ContextVar holds when the handler actually logs,
rather than evaluating it from the test's own context afterwards (which never touched it —
`TestClient` runs the ASGI app through a separate portal task).

Built as a minimal app from the same middleware `create_app` wires together, rather than the
real app, which needs a seeded container and has no route that raises on purpose.
"""

from __future__ import annotations

import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from arcadia_recommendation.infrastructure.observability.logging import _add_correlation
from arcadia_recommendation.presentation.http.errors import register_exception_handlers
from arcadia_recommendation.presentation.http.middleware import (
    CORRELATION_ID_HEADER,
    CorrelationIdMiddleware,
    PrometheusMetricsMiddleware,
    RequestLogMiddleware,
)


def _build_app(*, correlation_last: bool) -> FastAPI:
    """`correlation_last=True` reproduces `create_app`'s current (fixed) registration
    order; `False` reproduces the order that shipped the bug, for the negative case."""
    app = FastAPI()

    def add_correlation() -> None:
        app.add_middleware(CorrelationIdMiddleware)

    app.add_middleware(RequestLogMiddleware)
    if not correlation_last:
        add_correlation()
    app.add_middleware(PrometheusMetricsMiddleware, service="test-service")
    if correlation_last:
        add_correlation()

    register_exception_handlers(app)

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("deliberate failure for the test")

    @app.get("/ok")
    async def ok() -> dict[str, str]:
        return {"status": "fine"}

    return app


def test_the_error_log_line_carries_the_same_id() -> None:
    client = TestClient(_build_app(correlation_last=True), raise_server_exceptions=False)

    with capture_logs(processors=(structlog.contextvars.merge_contextvars, _add_correlation)) as logs:
        client.get("/boom", headers={CORRELATION_ID_HEADER: "test-correlation-xyz789"})

    unhandled = [e for e in logs if e.get("event") == "unhandled_error"]
    assert unhandled, "the unhandled-exception handler must log something"
    assert unhandled[0].get("correlation_id") == "test-correlation-xyz789", (
        f"the logged event did not carry the request's correlation id: {unhandled[0]!r} — "
        "CorrelationIdMiddleware must be the outermost middleware"
    )


def test_a_successful_request_still_gets_its_id_back() -> None:
    client = TestClient(_build_app(correlation_last=True), raise_server_exceptions=False)

    response = client.get("/ok", headers={CORRELATION_ID_HEADER: "test-correlation-ok"})

    assert response.status_code == 200
    assert response.headers[CORRELATION_ID_HEADER] == "test-correlation-ok"


def test_the_old_registration_order_reproduces_the_bug() -> None:
    """Pins the actual failure mode down: with the middleware in the order this shipped
    with, the id is lost. If this test ever starts failing, the bug it documents is a
    regression risk again even though the test above still passes."""
    client = TestClient(_build_app(correlation_last=False), raise_server_exceptions=False)

    with capture_logs(processors=(structlog.contextvars.merge_contextvars, _add_correlation)) as logs:
        client.get("/boom", headers={CORRELATION_ID_HEADER: "should-be-lost"})

    unhandled = [e for e in logs if e.get("event") == "unhandled_error"]
    assert unhandled
    assert unhandled[0].get("correlation_id") != "should-be-lost"
