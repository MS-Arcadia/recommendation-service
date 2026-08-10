from opentelemetry import trace
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)

from arcadia_recommendation.infrastructure.config.settings import Settings

_TRACER_NAME = "arcadia.recommendation"


def configure_tracing(settings: Settings) -> None:
    """A provider is always installed so every log line carries a traceId, but nothing is exported unless
    asked for: OTLP when an endpoint is configured, console when OTEL_CONSOLE_EXPORT is set. Dumping spans to
    stdout by default would bury the output of a local run and of the test suite.
    TODO(integration): OBSERVABILITY — point OTLP at the cluster collector in the observability namespace
    (architecture §9); add a Grafana dashboard and Prometheus alert rules for outbox lag and DLQ depth."""
    if isinstance(trace.get_tracer_provider(), TracerProvider):
        return
    resource = Resource.create({SERVICE_NAME: settings.service_name})
    provider = TracerProvider(resource=resource)
    processor = _processor(settings)
    if processor is not None:
        provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)


def _processor(settings: Settings) -> SpanProcessor | None:
    if settings.otel_exporter_otlp_endpoint is not None:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        return BatchSpanProcessor(
            OTLPSpanExporter(endpoint=f"{settings.otel_exporter_otlp_endpoint}/v1/traces")
        )
    if settings.otel_console_export:
        return SimpleSpanProcessor(ConsoleSpanExporter())
    return None


def tracer() -> trace.Tracer:
    return trace.get_tracer(_TRACER_NAME)
