"""Domain errors. The API maps each to one HTTP status, so services never import web concerns."""

from __future__ import annotations


class FraudAuditError(Exception):
    """Base for every error this service raises on purpose."""


class InvalidInputError(FraudAuditError):
    """The request is well-formed JSON but breaks a domain rule, such as an event type the pack does not define."""


class NotFoundError(FraudAuditError):
    pass


class ConflictError(FraudAuditError):
    """The resource is not in a state that allows the request, such as deciding a Case twice."""


class InvestigationError(FraudAuditError):
    """An Investigation could not run to Await Approval for an infrastructure reason; it can be retried."""


class LlmError(FraudAuditError):
    """A model call failed after its retries and any fallback."""
