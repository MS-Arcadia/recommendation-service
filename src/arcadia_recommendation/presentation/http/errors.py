from fastapi import FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from arcadia_recommendation.application.errors import AuthenticationError, AuthorizationError
from arcadia_recommendation.domain.shared.errors import Forbidden, InvariantViolation, NotFound
from arcadia_recommendation.infrastructure.observability.correlation import current_correlation_id
from arcadia_recommendation.infrastructure.observability.logging import get_logger
from arcadia_recommendation.presentation.http.deps.auth import DebugAuthDisabledError

_logger = get_logger(__name__)

_PROBLEM_MEDIA_TYPE = "application/problem+json"

_STATUS_MAP: dict[type[Exception], int] = {
    InvariantViolation: status.HTTP_422_UNPROCESSABLE_CONTENT,
    NotFound: status.HTTP_404_NOT_FOUND,
    Forbidden: status.HTTP_403_FORBIDDEN,
    AuthenticationError: status.HTTP_401_UNAUTHORIZED,
    AuthorizationError: status.HTTP_403_FORBIDDEN,
    DebugAuthDisabledError: status.HTTP_403_FORBIDDEN,
}

_TITLE_MAP: dict[type[Exception], str] = {
    InvariantViolation: "INVALID_ARGUMENT",
    NotFound: "NOT_FOUND",
    Forbidden: "PERMISSION_DENIED",
    AuthenticationError: "UNAUTHENTICATED",
    AuthorizationError: "PERMISSION_DENIED",
    DebugAuthDisabledError: "PERMISSION_DENIED",
}


def register_exception_handlers(app: FastAPI) -> None:
    """One place that turns exceptions into responses, so no router needs a try/except of its own. The body is
    an RFC 7807 problem document — `{type, title, status, detail, reason, details, trace_id}`, served as
    `application/problem+json` — the same shape every service on the platform returns. Authorization failures
    map to 403 rather than 401: the caller was identified and is simply not permitted, so there is nothing to
    re-authenticate with and a 401 would invite a pointless retry."""

    for exception_type in _STATUS_MAP:
        app.add_exception_handler(exception_type, _handle_known_error)

    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(Exception, _handle_unexpected_error)


def _problem(status_code: int, title: str, detail: str, *, reason: str = "") -> JSONResponse:
    body: dict[str, object] = {
        "type": "about:blank",
        "title": title,
        "status": status_code,
        "detail": detail,
        "reason": reason or title,
        "details": {},
        "trace_id": current_correlation_id() or "",
    }
    return JSONResponse(status_code=status_code, content=body, media_type=_PROBLEM_MEDIA_TYPE)


async def _handle_known_error(_request: Request, exc: Exception) -> Response:
    status_code = _STATUS_MAP.get(type(exc), status.HTTP_400_BAD_REQUEST)
    title = _TITLE_MAP.get(type(exc), "BAD_REQUEST")
    if isinstance(exc, DebugAuthDisabledError):
        _logger.error("debug_auth_header_rejected", error=str(exc))
        response = _problem(status_code, title, "Debug authentication is not available.")
    else:
        response = _problem(status_code, title, str(exc))
    if isinstance(exc, AuthenticationError):
        response.headers["WWW-Authenticate"] = "Bearer"
    return response


async def _handle_validation_error(_request: Request, exc: Exception) -> Response:
    detail = "Request body or parameters failed validation."
    if isinstance(exc, RequestValidationError):
        detail = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'][1:])}: {error['msg']}" for error in exc.errors()
        )
    return _problem(
        status.HTTP_422_UNPROCESSABLE_CONTENT, "INVALID_ARGUMENT", detail, reason="VALIDATION_FAILED"
    )


async def _handle_unexpected_error(request: Request, exc: Exception) -> Response:
    _logger.exception(
        "unhandled_error",
        method=request.method,
        path=request.url.path,
        error_type=type(exc).__name__,
    )
    return _problem(
        status.HTTP_500_INTERNAL_SERVER_ERROR, "INTERNAL", "Internal server error.", reason="INTERNAL"
    )
