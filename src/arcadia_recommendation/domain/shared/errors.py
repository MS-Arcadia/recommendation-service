class DomainError(Exception):
    """Root of every business-rule failure; deliberately carries no HTTP or transport semantics."""


class InvariantViolation(DomainError):
    """An aggregate or value object was asked to enter a state its rules forbid."""


class NotFound(DomainError):
    """A referenced aggregate does not exist."""


class Forbidden(DomainError):
    """The acting user is not permitted to perform this operation on this aggregate."""
