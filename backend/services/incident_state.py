from datetime import datetime
from typing import Final

from backend.domain.enums import (
    NON_TERMINAL_INCIDENT_STATUSES,
    TERMINAL_INCIDENT_STATUSES,
    IncidentStatus,
)
from backend.domain.errors import Err, Ok, Result, StateTransitionError
from backend.domain.models import Incident

_STATUS_FIELD: Final[str] = "status"
_UPDATED_AT_FIELD: Final[str] = "updated_at"

_CONTEXT_CURRENT: Final[str] = "current"
_CONTEXT_TARGET: Final[str] = "target"
_CONTEXT_NOW: Final[str] = "now"


LEGAL_TRANSITIONS: Final[frozenset[tuple[IncidentStatus, IncidentStatus]]] = (
    frozenset(
        {
            (IncidentStatus.PENDING, IncidentStatus.RUNNING),
            (IncidentStatus.PENDING, IncidentStatus.COMPLETED),
            (IncidentStatus.RUNNING, IncidentStatus.AWAITING_REVIEW),
            (IncidentStatus.RUNNING, IncidentStatus.CONTESTED),
            (IncidentStatus.RUNNING, IncidentStatus.FAILED),
            (IncidentStatus.AWAITING_REVIEW, IncidentStatus.COMPLETED),
            (IncidentStatus.AWAITING_REVIEW, IncidentStatus.REJECTED),
        }
    )
)


def is_terminal(status: IncidentStatus) -> bool:
    return status in TERMINAL_INCIDENT_STATUSES


def can_transition(current: IncidentStatus, target: IncidentStatus) -> bool:
    return (current, target) in LEGAL_TRANSITIONS


def _is_tz_aware(moment: datetime) -> bool:
    return moment.tzinfo is not None and moment.tzinfo.utcoffset(moment) is not None


def transition(
    incident: Incident, target: IncidentStatus, now: datetime
) -> Result[Incident, StateTransitionError]:
    if not _is_tz_aware(now):
        return Err(
            StateTransitionError(
                "transition requires a timezone-aware timestamp",
                {_CONTEXT_TARGET: target.value, _CONTEXT_NOW: now.isoformat()},
            )
        )
    if not can_transition(incident.status, target):
        return Err(
            StateTransitionError(
                "illegal incident status transition",
                {
                    _CONTEXT_CURRENT: incident.status.value,
                    _CONTEXT_TARGET: target.value,
                },
            )
        )
    updated: Incident = incident.model_copy(
        update={_STATUS_FIELD: target, _UPDATED_AT_FIELD: now}
    )
    return Ok(updated)


def _reaches_terminal(source: IncidentStatus) -> bool:
    frontier: list[IncidentStatus] = [source]
    visited: set[IncidentStatus] = set()
    while frontier:
        node = frontier.pop()
        if node in visited:
            continue
        visited.add(node)
        if is_terminal(node):
            return True
        frontier.extend(
            edge_target
            for edge_source, edge_target in LEGAL_TRANSITIONS
            if edge_source == node
        )
    return False


assert all(_reaches_terminal(status) for status in NON_TERMINAL_INCIDENT_STATUSES)
assert all(
    not can_transition(status, target)
    for status in TERMINAL_INCIDENT_STATUSES
    for target in IncidentStatus
)
