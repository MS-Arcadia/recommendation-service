from dataclasses import dataclass
from enum import StrEnum

from arcadia_recommendation.domain.shared.ids import UserId


class Role(StrEnum):
    """The four roles of Requirements §1.1, issued by Auth Service; this service consumes them and never
    assigns them. Every user holds exactly one."""

    BASIC_USER = "BASIC_USER"
    DEVELOPER = "DEVELOPER"
    SUPPORT = "SUPPORT"
    ADMIN = "ADMIN"


@dataclass(frozen=True, slots=True)
class Actor:
    """The authenticated caller, resolved from JWT claims in the presentation layer and passed inward."""

    user_id: UserId
    role: Role
    is_banned: bool = False

    @property
    def is_moderator(self) -> bool:
        return self.role in (Role.SUPPORT, Role.ADMIN)
