from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from backend.domain.determinism import Clock
from backend.domain.enums import FileDecisionKind, IncidentStatus, ReviewDecision
from backend.domain.errors import DepCoverError, Err, Ok, Result
from backend.domain.models import FileDecision, Review, Transplant
from backend.ports.auth import AuthenticatedUser
from backend.ports.record_store import RecordStore
from backend.services.incident_state import is_terminal, transition

_REJECT_REASON_SEPARATOR: Final[str] = "\n"
_PATH_SEPARATOR: Final[str] = ","

_MSG_REJECT_REQUIRES_REASON: Final[str] = "a rejection requires at least one reason"
_MSG_ACCEPT_ALL_CONTRADICTS_REJECT: Final[str] = (
    "an accept-all decision cannot include a per-file rejection"
)
_MSG_ACCEPT_ALL_INCOMPLETE: Final[str] = (
    "an accept-all decision must accept every transplanted file"
)
_MSG_NOT_REVIEWABLE: Final[str] = "incident is not awaiting review"

_CTX_TRANSPLANT_ID: Final[str] = "transplant_id"
_CTX_INCIDENT_ID: Final[str] = "incident_id"
_CTX_DECISION: Final[str] = "decision"
_CTX_REJECTED_PATHS: Final[str] = "rejected_paths"
_CTX_UNCOVERED_PATHS: Final[str] = "uncovered_paths"
_CTX_STATUS: Final[str] = "status"


@dataclass(frozen=True)
class _ReviewPlan:
    target: IncidentStatus
    reason: str | None


def _reasons(per_file: Sequence[FileDecision]) -> tuple[str, ...]:
    return tuple(
        decision.reason
        for decision in per_file
        if decision.reason is not None and decision.reason.strip() != ""
    )


def _rejected_paths(per_file: Sequence[FileDecision]) -> tuple[str, ...]:
    return tuple(
        decision.path
        for decision in per_file
        if decision.kind is FileDecisionKind.REJECT
    )


def _uncovered_paths(
    transplant: Transplant, per_file: Sequence[FileDecision]
) -> tuple[str, ...]:
    accepted = frozenset(
        decision.path
        for decision in per_file
        if decision.kind is FileDecisionKind.ACCEPT
    )
    return tuple(
        file_diff.path
        for file_diff in transplant.diff
        if file_diff.path not in accepted
    )


def _plan(
    transplant: Transplant,
    decision: ReviewDecision,
    per_file: Sequence[FileDecision],
) -> Result[_ReviewPlan, DepCoverError]:
    if decision is ReviewDecision.REJECT:
        reasons = _reasons(per_file)
        if len(reasons) == 0:
            return Err(
                DepCoverError(
                    _MSG_REJECT_REQUIRES_REASON,
                    {
                        _CTX_TRANSPLANT_ID: transplant.id,
                        _CTX_DECISION: decision.value,
                    },
                )
            )
        return Ok(
            _ReviewPlan(
                target=IncidentStatus.REJECTED,
                reason=_REJECT_REASON_SEPARATOR.join(reasons),
            )
        )
    rejected = _rejected_paths(per_file)
    if len(rejected) > 0:
        return Err(
            DepCoverError(
                _MSG_ACCEPT_ALL_CONTRADICTS_REJECT,
                {
                    _CTX_TRANSPLANT_ID: transplant.id,
                    _CTX_REJECTED_PATHS: _PATH_SEPARATOR.join(rejected),
                },
            )
        )
    uncovered = _uncovered_paths(transplant, per_file)
    if len(uncovered) > 0:
        return Err(
            DepCoverError(
                _MSG_ACCEPT_ALL_INCOMPLETE,
                {
                    _CTX_TRANSPLANT_ID: transplant.id,
                    _CTX_UNCOVERED_PATHS: _PATH_SEPARATOR.join(uncovered),
                },
            )
        )
    return Ok(_ReviewPlan(target=IncidentStatus.COMPLETED, reason=None))


class ReviewService:
    def __init__(self, store: RecordStore, clock: Clock) -> None:
        self._store: RecordStore = store
        self._clock: Clock = clock

    def submit(
        self,
        user: AuthenticatedUser,
        transplant: Transplant,
        decision: ReviewDecision,
        per_file: Sequence[FileDecision],
    ) -> Result[tuple[Review, IncidentStatus], DepCoverError]:
        recorded = tuple(per_file)
        plan_result = _plan(transplant, decision, recorded)
        if isinstance(plan_result, Err):
            return plan_result
        plan = plan_result.value

        review = Review(
            transplant_id=transplant.id,
            user_id=user.id,
            decision=decision,
            per_file=recorded,
            reason=plan.reason,
        )

        incident_result = self._store.get_incident(transplant.incident_id)
        if isinstance(incident_result, Err):
            return Err(incident_result.error)
        incident = incident_result.value

        if is_terminal(incident.status):
            return Ok((review, incident.status))

        if incident.status is not IncidentStatus.AWAITING_REVIEW:
            return Err(
                DepCoverError(
                    _MSG_NOT_REVIEWABLE,
                    {
                        _CTX_INCIDENT_ID: incident.id,
                        _CTX_STATUS: incident.status.value,
                    },
                )
            )

        transitioned = transition(incident, plan.target, self._clock.now())
        if isinstance(transitioned, Err):
            return Err(transitioned.error)

        saved = self._store.save_review(review)
        if isinstance(saved, Err):
            return Err(saved.error)

        updated = self._store.update_incident(
            transitioned.value, IncidentStatus.AWAITING_REVIEW
        )
        if isinstance(updated, Err):
            return Err(updated.error)

        return Ok((saved.value, updated.value.status))


__all__ = ("ReviewService",)
