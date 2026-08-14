from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from arcadia_recommendation.composition import Container
from arcadia_recommendation.infrastructure.observability.logging import configure_logging, get_logger
from arcadia_recommendation.infrastructure.observability.metrics import RecommendationMetrics
from arcadia_recommendation.infrastructure.observability.tracing import configure_tracing
from arcadia_recommendation.presentation.consumers.registry import register_consumers
from arcadia_recommendation.presentation.http import health
from arcadia_recommendation.presentation.http.errors import register_exception_handlers
from arcadia_recommendation.presentation.http.middleware import (
    CorrelationIdMiddleware,
    PrometheusMetricsMiddleware,
    RequestLogMiddleware,
)
from arcadia_recommendation.presentation.http.routers import admin, recommendations

API_PREFIX = "/v1"

_logger = get_logger(__name__)


def create_app(container: Container) -> FastAPI:
    """Everything is mounted under /v1, like every other service on the platform, so a gateway routing by path
    prefix and a client generated from several of these OpenAPI documents both behave. Health probes sit
    outside the version prefix because an orchestrator's probe config should not move when the API does.

    CorrelationIdMiddleware is added last, deliberately: Starlette's add_middleware() prepends, so whichever
    middleware is registered last ends up outermost — directly under ServerErrorMiddleware, with nothing else
    between them. It used to sit between RequestLog and PrometheusMetrics, with metrics outer.
    BaseHTTPMiddleware runs everything inside it in a separate anyio task, and on an exception re-raises it
    back in the *parent* task rather than the one where it originated — so bind_correlation_id() was invisible
    by the time register_exception_handlers's catch-all Exception handler (which Starlette pulls out into
    ServerErrorMiddleware regardless of where it is registered) tried to log with it. Being outermost removes
    that hop: the id is bound in the same task ServerErrorMiddleware itself runs in."""
    settings = container.settings
    configure_logging(settings)
    configure_tracing(settings)
    _assert_debug_auth_is_local_only(container)
    _warn_about_in_memory_backends(container)

    app = FastAPI(
        title="Arcadia Recommendation Service",
        version="0.1.0",
        docs_url=f"{API_PREFIX}/docs",
        openapi_url=f"{API_PREFIX}/openapi.json",
        lifespan=_lifespan,
    )
    app.state.container = container
    app.state.metrics = RecommendationMetrics()

    app.add_middleware(RequestLogMiddleware)
    app.add_middleware(PrometheusMetricsMiddleware, service=settings.service_name)
    app.add_middleware(CorrelationIdMiddleware)

    app.include_router(health.router)
    app.include_router(recommendations.router, prefix=API_PREFIX)
    app.include_router(admin.router, prefix=API_PREFIX)

    register_exception_handlers(app)
    FastAPIInstrumentor.instrument_app(app)
    return app


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    container: Container = app.state.container
    metrics: RecommendationMetrics = app.state.metrics
    adapters = container.adapters
    metrics.observe_outbox(
        pending=lambda: adapters.dispatcher.pending_count,
        dlq_depth=lambda: adapters.dispatcher.dlq_depth,
        known_games=lambda: adapters.known_games.value,
    )
    register_consumers(container, adapters.subscriber)
    for component in adapters.lifecycle:
        await component.start()
    _logger.info(
        "service_started",
        app_env=container.settings.app_env,
        persistence=container.settings.persistence_backend,
        messaging=container.settings.messaging_backend,
        generation_enabled=container.settings.generation_enabled,
    )
    try:
        yield
    finally:
        for component in reversed(adapters.lifecycle):
            await component.stop()
        _logger.info("service_stopped")


def _warn_about_in_memory_backends(container: Container) -> None:
    """Warned rather than refused. Running staging on the fakes is a legitimate choice while the siblings are
    still being built, but it is one to make on purpose: in-memory state does not survive a restart, so every
    learned preference is lost, and an in-process bus consumes from nobody — this service would then serve
    the fallback for ever while looking perfectly healthy."""
    settings = container.settings
    if settings.is_local:
        return
    if not settings.uses_postgres:
        _logger.warning("volatile_persistence_backend", app_env=settings.app_env)
    if not settings.uses_kafka:
        _logger.warning("no_broker_configured", app_env=settings.app_env)
    if not settings.generation_enabled:
        _logger.warning("generation_scheduler_disabled_outside_local", app_env=settings.app_env)


def _assert_debug_auth_is_local_only(container: Container) -> None:
    settings = container.settings
    if settings.app_env != "local" and settings.identity_backend == "fake":
        raise RuntimeError(
            f"IDENTITY_BACKEND=fake exposes the debug auth header; refusing to start with "
            f"APP_ENV={settings.app_env}"
        )
