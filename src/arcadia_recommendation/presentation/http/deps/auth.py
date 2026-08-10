from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from arcadia_recommendation.application.errors import AuthenticationError, AuthorizationError
from arcadia_recommendation.domain.shared.actor import Actor, Role
from arcadia_recommendation.domain.shared.ids import UserId
from arcadia_recommendation.infrastructure.observability.correlation import bind_actor_id
from arcadia_recommendation.presentation.http.deps.container import ContainerDep

DEBUG_USER_HEADER = "X-Debug-User"

_bearer = HTTPBearer(auto_error=False, description="Access token issued by the Auth service")


class DebugAuthDisabledError(RuntimeError):
    """Raised when the debug auth header is offered outside a local run. Kept as a hard failure rather than a
    silent fallback: a debug auth header reachable in production is the worst bug this service could ship."""


async def optional_actor(
    container: ContainerDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
    x_debug_user: Annotated[str | None, Header(alias=DEBUG_USER_HEADER)] = None,
) -> Actor | None:
    """Yields None for an anonymous caller rather than rejecting, because `/games/{id}/similar` is public.

    The token is verified here even though the Gateway already verified it: architecture §8.1 asks for
    validation at the Gateway *and* every service, because a call that reaches a pod directly must not be
    trusted more than one that did not.
    """
    tokens = container.adapters.tokens
    if tokens is not None and credentials is not None:
        if credentials.scheme.lower() != "bearer" or not credentials.credentials:
            raise AuthenticationError("Authorization must be a Bearer token")
        actor = tokens.verify(credentials.credentials)
        bind_actor_id(str(actor.user_id))
        return actor
    if x_debug_user is None:
        return None
    settings = container.settings
    if not (settings.is_local and settings.identity_backend == "fake"):
        raise DebugAuthDisabledError(
            f"{DEBUG_USER_HEADER} is a local-only development path; "
            f"APP_ENV={settings.app_env} and IDENTITY_BACKEND={settings.identity_backend}"
        )
    try:
        user_id = UserId(UUID(x_debug_user))
    except ValueError as exc:
        raise AuthorizationError(f"{DEBUG_USER_HEADER} must be a UUID") from exc
    bind_actor_id(str(user_id))
    return Actor(user_id=user_id, role=Role.BASIC_USER)


async def current_actor(actor: Annotated[Actor | None, Depends(optional_actor)]) -> Actor:
    if actor is None:
        raise AuthorizationError("this operation requires an authenticated caller")
    return actor


def require_roles(*roles: Role) -> object:
    async def dependency(actor: Annotated[Actor, Depends(current_actor)]) -> Actor:
        if actor.role not in roles:
            allowed = ", ".join(str(role) for role in roles)
            raise AuthorizationError(f"this operation requires one of: {allowed}")
        return actor

    return Depends(dependency)


OptionalActorDep = Annotated[Actor | None, Depends(optional_actor)]
ActorDep = Annotated[Actor, Depends(current_actor)]
ModeratorDep = Annotated[Actor, require_roles(Role.SUPPORT, Role.ADMIN)]
