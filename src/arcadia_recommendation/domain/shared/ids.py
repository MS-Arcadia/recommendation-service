from dataclasses import dataclass
from typing import Self
from uuid import UUID

from arcadia_recommendation.domain.shared.errors import InvariantViolation


@dataclass(frozen=True, slots=True)
class EntityId:
    """Typed UUID wrapper; distinct subclasses stop one id being passed where another belongs."""

    value: UUID

    @classmethod
    def parse(cls, raw: str) -> Self:
        try:
            return cls(UUID(raw))
        except ValueError as exc:
            raise InvariantViolation(f"{cls.__name__} must be a UUID, got {raw!r}") from exc

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True)
class UserId(EntityId):
    """Identity of a user; owned by Auth Service, referenced here only."""


@dataclass(frozen=True, slots=True)
class GameId(EntityId):
    """Identity of a game; owned by Catalog Service, referenced here only."""


@dataclass(frozen=True, slots=True)
class RecommendationId(EntityId):
    """Identity of one generated Recommendation row."""
