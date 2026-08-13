import logging
from collections.abc import MutableMapping
from typing import Any

import structlog
from opentelemetry import trace

from arcadia_recommendation.infrastructure.config.settings import Settings
from arcadia_recommendation.infrastructure.observability.correlation import (
    current_actor_id,
    current_correlation_id,
)

REDACTED = "[redacted]"
_SENSITIVE_KEYS = frozenset({"authorization", "cookie", "set-cookie", "x-api-key", "token", "password"})

type EventDict = MutableMapping[str, Any]


def _add_service(service_name: str) -> Any:
    def processor(_logger: Any, _name: str, event_dict: EventDict) -> EventDict:
        event_dict["service"] = service_name
        return event_dict

    return processor


def _add_correlation(_logger: Any, _name: str, event_dict: EventDict) -> EventDict:
    """Stamp the request's correlation and actor onto the line, in snake_case.

    The spelling is the platform's, not this service's preference. correlation_id
    is the field a cross-service query filters on, and while this service emitted
    correlationId a LogQL `| json | correlation_id="..."` returned nothing for it —
    so a trace through the platform lost whichever hops ran here. It also left a
    single line internally inconsistent, since the request middleware already
    passes actor_id in snake_case.
    """
    correlation_id = current_correlation_id()
    if correlation_id is not None:
        event_dict["correlation_id"] = correlation_id
    actor_id = current_actor_id()
    if actor_id is not None:
        event_dict["actor_id"] = actor_id
    return event_dict


def _add_trace_context(_logger: Any, _name: str, event_dict: EventDict) -> EventDict:
    span = trace.get_current_span()
    context = span.get_span_context()
    if context.is_valid:
        event_dict["trace_id"] = format(context.trace_id, "032x")
        event_dict["span_id"] = format(context.span_id, "016x")
    return event_dict


def _redact(_logger: Any, _name: str, event_dict: EventDict) -> EventDict:
    for key in list(event_dict):
        if key.lower() in _SENSITIVE_KEYS:
            event_dict[key] = REDACTED
        elif key == "headers" and isinstance(event_dict[key], dict):
            event_dict[key] = {
                name: (REDACTED if name.lower() in _SENSITIVE_KEYS else value)
                for name, value in event_dict[key].items()
            }
    return event_dict


def configure_logging(settings: Settings) -> None:
    logging.basicConfig(format="%(message)s", level=settings.log_level)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True, key="timestamp"),
            _add_service(settings.service_name),
            _add_correlation,
            _add_trace_context,
            _redact,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[settings.log_level]
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
