"""Complaint State Machine & Legal Transition Engine.

Strictly defines the Cantonment Board complaint lifecycle:
  NEW -> AI_PROCESSING -> (VERIFIED | PENDING_VERIFICATION) -> ASSIGNED
      -> IN_PROGRESS -> PENDING_DEPARTMENT_VERIFICATION
      -> (RESOLVED | RESOLUTION_REJECTED)
      -> PENDING_CUSTOMER_CONFIRMATION -> (CUSTOMER_CONFIRMED | REOPENED)
      -> CLOSED

Prevents invalid state jumps and guarantees auditability.
"""

from typing import Set, Dict

STATUS_NEW = "NEW"
STATUS_AI_PROCESSING = "AI_PROCESSING"
STATUS_PENDING_VERIFICATION = "PENDING_VERIFICATION"
STATUS_VERIFIED = "VERIFIED"
STATUS_ASSIGNED = "ASSIGNED"
STATUS_IN_PROGRESS = "IN_PROGRESS"
STATUS_PENDING_DEPARTMENT_VERIFICATION = "PENDING_DEPARTMENT_VERIFICATION"
STATUS_RESOLUTION_REJECTED = "RESOLUTION_REJECTED"
STATUS_RESOLVED = "RESOLVED"
STATUS_PENDING_CUSTOMER_CONFIRMATION = "PENDING_CUSTOMER_CONFIRMATION"
STATUS_CUSTOMER_CONFIRMED = "CUSTOMER_CONFIRMED"
STATUS_REOPENED = "REOPENED"
STATUS_ESCALATED = "ESCALATED"
STATUS_CLOSED = "CLOSED"
STATUS_CANCELLED = "CANCELLED"

VALID_STATUSES = {
    STATUS_NEW,
    STATUS_AI_PROCESSING,
    STATUS_PENDING_VERIFICATION,
    STATUS_VERIFIED,
    STATUS_ASSIGNED,
    STATUS_IN_PROGRESS,
    STATUS_PENDING_DEPARTMENT_VERIFICATION,
    STATUS_RESOLUTION_REJECTED,
    STATUS_RESOLVED,
    STATUS_PENDING_CUSTOMER_CONFIRMATION,
    STATUS_CUSTOMER_CONFIRMED,
    STATUS_REOPENED,
    STATUS_ESCALATED,
    STATUS_CLOSED,
    STATUS_CANCELLED,
}

# Permitted state transitions
TRANSITIONS: Dict[str, Set[str]] = {
    STATUS_NEW: {
        STATUS_AI_PROCESSING,
        STATUS_PENDING_VERIFICATION,
        STATUS_VERIFIED,
        STATUS_ASSIGNED,
        STATUS_CANCELLED,
    },
    STATUS_AI_PROCESSING: {
        STATUS_PENDING_VERIFICATION,
        STATUS_VERIFIED,
        STATUS_ASSIGNED,
        STATUS_CANCELLED,
    },
    STATUS_PENDING_VERIFICATION: {
        STATUS_VERIFIED,
        STATUS_ASSIGNED,
        STATUS_CANCELLED,
    },
    STATUS_VERIFIED: {
        STATUS_ASSIGNED,
        STATUS_IN_PROGRESS,
        STATUS_CANCELLED,
    },
    STATUS_ASSIGNED: {
        STATUS_IN_PROGRESS,
        STATUS_ESCALATED,
        STATUS_CANCELLED,
    },
    STATUS_IN_PROGRESS: {
        STATUS_PENDING_DEPARTMENT_VERIFICATION,
        STATUS_ESCALATED,
        STATUS_CANCELLED,
    },
    STATUS_PENDING_DEPARTMENT_VERIFICATION: {
        STATUS_RESOLVED,
        STATUS_RESOLUTION_REJECTED,
        STATUS_PENDING_CUSTOMER_CONFIRMATION,
    },
    STATUS_RESOLUTION_REJECTED: {
        STATUS_IN_PROGRESS,
        STATUS_ASSIGNED,
        STATUS_ESCALATED,
    },
    STATUS_RESOLVED: {
        STATUS_PENDING_CUSTOMER_CONFIRMATION,
        STATUS_CUSTOMER_CONFIRMED,
        STATUS_CLOSED,
        STATUS_REOPENED,
    },
    STATUS_PENDING_CUSTOMER_CONFIRMATION: {
        STATUS_CUSTOMER_CONFIRMED,
        STATUS_REOPENED,
        STATUS_CLOSED,
    },
    STATUS_CUSTOMER_CONFIRMED: {
        STATUS_CLOSED,
    },
    STATUS_REOPENED: {
        STATUS_ASSIGNED,
        STATUS_IN_PROGRESS,
        STATUS_PENDING_VERIFICATION,
        STATUS_ESCALATED,
    },
    STATUS_ESCALATED: {
        STATUS_IN_PROGRESS,
        STATUS_PENDING_DEPARTMENT_VERIFICATION,
        STATUS_ASSIGNED,
        STATUS_CLOSED,
    },
    STATUS_CLOSED: {
        STATUS_REOPENED,  # Citizen can reopen within grace window if unsatisfied
    },
    STATUS_CANCELLED: set(),
}


class InvalidTransitionError(Exception):
    def __init__(self, current_status: str, target_status: str, reason: str = ""):
        super().__init__(
            f"Invalid status transition from '{current_status}' to '{target_status}'. {reason}".strip()
        )
        self.current_status = current_status
        self.target_status = target_status
        self.reason = reason


def can_transition(current_status: str, target_status: str) -> bool:
    """Return True if transition from current to target status is valid."""
    if target_status not in VALID_STATUSES:
        return False
    if current_status == target_status:
        return True
    allowed = TRANSITIONS.get(current_status, set())
    return target_status in allowed


def validate_transition(current_status: str, target_status: str, reason: str = "") -> None:
    """Raise InvalidTransitionError if transition is not permitted."""
    if not can_transition(current_status, target_status):
        raise InvalidTransitionError(current_status, target_status, reason)
