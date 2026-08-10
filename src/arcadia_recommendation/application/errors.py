class ApplicationError(Exception):
    """Root of failures that are about orchestration rather than business rules. Kept separate from
    DomainError because the two hierarchies map to different HTTP families in the presentation layer."""


class AuthenticationError(ApplicationError):
    """The caller presented a credential this service could not verify."""


class AuthorizationError(ApplicationError):
    """The caller is authenticated but lacks the role required for this operation."""
