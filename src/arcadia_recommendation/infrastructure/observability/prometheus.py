"""Prometheus collectors for direct scraping.

The platform's Prometheus scrapes each service's `/metrics` directly — no OTel collector in the loop — so
this sits alongside, not instead of, the OpenTelemetry OTLP export configured in `tracing.py` and the
domain-level counters in `metrics.py`. Names and labels mirror catalog-service's `app/platform/http.py`
exactly, so a dashboard or alert rule built against one service works unmodified against this one."""

from prometheus_client import Counter, Histogram

http_requests_total = Counter(
    "arcadia_http_requests_total",
    "HTTP requests handled.",
    ["service", "method", "route", "status"],
)

http_request_duration_seconds = Histogram(
    "arcadia_http_request_duration_seconds",
    "How long HTTP requests took.",
    ["service", "method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)
