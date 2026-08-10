import time
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from arcadia_recommendation.infrastructure.observability.correlation import (
    CORRELATION_ID_HEADER,
    bind_actor_id,
    bind_correlation_id,
    current_actor_id,
    new_correlation_id,
)
from arcadia_recommendation.infrastructure.observability.logging import get_logger
from arcadia_recommendation.infrastructure.observability.prometheus import (
    http_request_duration_seconds,
    http_requests_total,
)

_logger = get_logger("http")


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Accepts an inbound correlation id or mints one, binds it for the request, and echoes it back. The same
    id is stamped onto every outbox record, which is what lets one request be followed from HTTP entry through
    the broker into Profile."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        correlation_id = request.headers.get(CORRELATION_ID_HEADER) or new_correlation_id()
        bind_correlation_id(correlation_id)
        bind_actor_id(None)
        response = await call_next(request)
        response.headers[CORRELATION_ID_HEADER] = correlation_id
        return response


class RequestLogMiddleware(BaseHTTPMiddleware):
    """One structured line per request. Bodies are never logged — only route, status and latency."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            _logger.exception(
                "request_failed",
                method=request.method,
                path=request.url.path,
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
            )
            raise
        _logger.info(
            "request_completed",
            method=request.method,
            path=request.url.path,
            route=_route_template(request),
            status=response.status_code,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            actor_id=current_actor_id(),
        )
        return response


class PrometheusMetricsMiddleware(BaseHTTPMiddleware):
    """Records the request-count and request-duration series that `/metrics` exposes for direct Prometheus
    scraping. Labelled by route template, never the concrete path — labelling by path would mint a new time
    series per post or comment id and eventually take Prometheus down."""

    def __init__(self, app: ASGIApp, *, service: str) -> None:
        super().__init__(app)
        self._service = service

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        started = time.perf_counter()
        response = await call_next(request)
        elapsed = time.perf_counter() - started
        route = _route_template(request)
        http_requests_total.labels(self._service, request.method, route, str(response.status_code)).inc()
        http_request_duration_seconds.labels(self._service, request.method, route).observe(elapsed)
        return response


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    path_format = getattr(route, "path_format", None)
    return str(path_format) if path_format is not None else request.url.path
