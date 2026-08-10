from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from arcadia_recommendation.infrastructure.health import check
from arcadia_recommendation.presentation.http.deps.container import ContainerDep

router = APIRouter(tags=["Health"])


@router.get("/livez", include_in_schema=False)
async def livez() -> dict[str, str]:
    """The process is up. Nothing else is asserted, on purpose: a liveness probe that touches dependencies
    turns a brief downstream blip into a restart storm across every replica."""
    return {"status": "UP"}


@router.get("/readyz", include_in_schema=False)
async def readyz(container: ContainerDep) -> JSONResponse:
    """Can this service actually serve a request?

    Persistence and the outbox dispatcher are critical: without the first there is nothing to read, and
    without the second every RecommendationGenerated piles up unpublished.

    Redis is checked but never fatal, and the generation scheduler is reported without being fatal either.
    A replica whose sweep has stopped can still serve every stored list and still ingest every event; pulling
    it out of the load balancer would turn a stale recommendation into no recommendation, which is strictly
    worse. The signal is there to be alerted on, not to fail a probe.
    """
    adapters = container.adapters
    checks: dict[str, dict[str, str]] = {
        "persistence": await check(adapters.persistence_probe),
        "outbox_dispatcher": {"status": "UP" if adapters.dispatcher.is_running else "DOWN"},
    }
    if adapters.cache_probe is not None:
        checks["cache"] = await check(adapters.cache_probe, critical=False)
    if container.settings.generation_enabled:
        checks["generation_scheduler"] = {"status": "UP" if adapters.scheduler.is_running else "DEGRADED"}
    healthy = all(entry["status"] != "DOWN" for entry in checks.values())
    report = {
        "status": "UP" if healthy else "DOWN",
        "service": container.settings.service_name,
        "checks": checks,
    }
    return JSONResponse(status_code=200 if healthy else 503, content=report)


@router.get("/health", include_in_schema=False)
async def health(container: ContainerDep) -> dict[str, str]:
    """Kept as an alias so anything already pointing here does not break."""
    return {"status": "ok", "service": container.settings.service_name}


@router.get("/metrics", include_in_schema=False)
async def metrics_endpoint() -> Response:
    """Prometheus-format metrics for direct scraping. The platform's Prometheus hits this route on every
    service rather than going through an OTel collector, so this is additive alongside — not a replacement
    for — the OTLP export configured in `tracing.py`."""
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)
