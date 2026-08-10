from typing import Any

import jwt

from arcadia_recommendation.application.errors import AuthenticationError
from arcadia_recommendation.domain.shared.actor import Actor, Role
from arcadia_recommendation.domain.shared.ids import UserId

TOKEN_TYPE_CLAIM = "typ"  # noqa: S105 - a claim name, not a secret
ACCESS = "access"


class JwtAccessTokenVerifier:
    """Verifies the platform's access token locally — signature, `iss`, `aud`, `exp` — and turns its claims
    into an Actor. Verified here as well as at the Gateway on purpose: architecture §8.1 asks for validation
    at the Gateway *and* every service, because a gateway bypass inside the cluster must not become an
    authentication bypass.

    `typ` is checked. A refresh token carries a `sub` and would otherwise pass every other check, which would
    make a seven-day credential usable on endpoints meant for a fifteen-minute one."""

    def __init__(self, secret: str, algorithm: str, issuer: str, audience: str) -> None:
        self._secret = secret
        self._algorithm = algorithm
        self._issuer = issuer
        self._audience = audience

    def verify(self, token: str) -> Actor:
        claims = self._decode(token)
        if str(claims.get(TOKEN_TYPE_CLAIM, ACCESS)) != ACCESS:
            raise AuthenticationError("a refresh token is not accepted as an access token")
        subject = claims.get("sub")
        if subject is None:
            raise AuthenticationError("token carries no subject")
        try:
            role = Role(str(claims.get("role", Role.BASIC_USER)))
        except ValueError as exc:
            raise AuthenticationError(f"token carries an unknown role {claims.get('role')!r}") from exc
        return Actor(
            user_id=UserId.parse(str(subject)),
            role=role,
            is_banned=str(claims.get("state", "")).upper() == "BANNED",
        )

    def _decode(self, token: str) -> dict[str, Any]:
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                self._secret,
                algorithms=[self._algorithm],
                issuer=self._issuer,
                audience=self._audience,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise AuthenticationError(f"token is not valid: {exc}") from exc
        return claims
