"""Tests for backend.services.incident_state (Unit 19: Incident state machine).

Verifies the legal transition graph and the four contract guarantees of a
pure incident state machine:

* ``can_transition`` is total and equals membership in ``LEGAL_TRANSITIONS``.
* ``transition`` performs a minimal mutation (only ``status`` + ``updated_at``)
  on the legal edges and returns ``Err(StateTransitionError)`` otherwise.
* No terminal status has an outgoing edge; every non-terminal status still has
  a path to a terminal status (reachability, re-derived via an in-test BFS).
* ``is_terminal`` matches ``TERMINAL_INCIDENT_STATUSES`` exactly and naive
  ``now`` timestamps are rejected.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone, tzinfo

import pytest

from backend.domain.enums import (
    NON_TERMINAL_INCIDENT_STATUSES,
    TERMINAL_INCIDENT_STATUSES,
    IncidentStatus,
    StrategyKind,
    TriggerType,
)
from backend.domain.errors import Err, Ok, StateTransitionError
from backend.domain.models import Incident
from backend.services.incident_state import (
    LEGAL_TRANSITIONS,
    can_transition,
    is_terminal,
    transition,
)

# created_at is deliberately earlier than NOW so that a preserved created_at is
# distinguishable from an updated updated_at.
CREATED_AT: datetime = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
NOW: datetime = datetime(2026, 7, 7, 12, 0, 0, tzinfo=timezone.utc)
NAIVE_NOW: datetime = datetime(2026, 7, 7, 12, 0, 0)

ALL_STATUSES: tuple[IncidentStatus, ...] = tuple(IncidentStatus)


def _incident(
    status: IncidentStatus,
    *,
    chosen_strategy: StrategyKind | None = None,
    updated_at: datetime = CREATED_AT,
) -> Incident:
    """Build a fully-populated, tz-aware Incident in the given status."""
    return Incident(
        id="inc-1",
        repo_id="repo-1",
        trigger_type=TriggerType.MOCK_CVE,
        chosen_strategy=chosen_strategy,
        status=status,
        created_at=CREATED_AT,
        updated_at=updated_at,
    )


# --- Case 1: can_transition is total and matches LEGAL_TRANSITIONS ----------


@pytest.mark.parametrize("current", ALL_STATUSES)
@pytest.mark.parametrize("target", ALL_STATUSES)
def test_can_transition_is_total_and_matches_membership(
    current: IncidentStatus, target: IncidentStatus
) -> None:
    assert can_transition(current, target) == ((current, target) in LEGAL_TRANSITIONS)


def test_legal_transitions_is_exactly_the_seven_documented_edges() -> None:
    expected: frozenset[tuple[IncidentStatus, IncidentStatus]] = frozenset(
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
    assert LEGAL_TRANSITIONS == expected
    assert len(LEGAL_TRANSITIONS) == 7


def test_no_legal_edge_is_a_self_loop() -> None:
    assert all(src != dst for src, dst in LEGAL_TRANSITIONS)


def test_legal_edge_sources_are_all_non_terminal() -> None:
    sources = {src for src, _ in LEGAL_TRANSITIONS}
    assert sources <= NON_TERMINAL_INCIDENT_STATUSES


# --- Case 2: each legal edge -> Ok, minimal mutation, else byte-identical ---


@pytest.mark.parametrize("current,target", sorted(LEGAL_TRANSITIONS))
def test_legal_transition_returns_ok_with_minimal_mutation(
    current: IncidentStatus, target: IncidentStatus
) -> None:
    incident = _incident(current, chosen_strategy=StrategyKind.TRANSPLANT)

    result = transition(incident, target, NOW)

    assert isinstance(result, Ok)
    updated = result.value
    assert updated.status == target
    assert updated.updated_at == NOW

    # Every other field is byte-identical to the input incident.
    for field_name in Incident.model_fields:
        if field_name in {"status", "updated_at"}:
            continue
        assert getattr(updated, field_name) == getattr(incident, field_name), field_name

    # Equivalent whole-object assertion via the reference minimal mutation.
    assert updated == incident.model_copy(update={"status": target, "updated_at": NOW})


@pytest.mark.parametrize("current,target", sorted(LEGAL_TRANSITIONS))
def test_legal_transition_does_not_mutate_input_incident(
    current: IncidentStatus, target: IncidentStatus
) -> None:
    incident = _incident(current)
    snapshot = incident.model_copy()

    transition(incident, target, NOW)

    assert incident == snapshot
    assert incident.status == current
    assert incident.updated_at == CREATED_AT


@pytest.mark.parametrize("current,target", sorted(LEGAL_TRANSITIONS))
def test_legal_transition_is_deterministic(
    current: IncidentStatus, target: IncidentStatus
) -> None:
    incident = _incident(current)
    first = transition(incident, target, NOW)
    second = transition(incident, target, NOW)
    assert isinstance(first, Ok)
    assert isinstance(second, Ok)
    assert first.value == second.value


def test_now_equal_to_created_at_boundary_is_still_ok() -> None:
    incident = _incident(IncidentStatus.PENDING)
    result = transition(incident, IncidentStatus.RUNNING, CREATED_AT)
    assert isinstance(result, Ok)
    assert result.value.updated_at == CREATED_AT
    assert result.value.created_at == CREATED_AT


# --- Case 3: illegal transitions -> Err(StateTransitionError) ---------------


ILLEGAL_SAMPLES: tuple[tuple[IncidentStatus, IncidentStatus], ...] = (
    (IncidentStatus.COMPLETED, IncidentStatus.RUNNING),
    (IncidentStatus.PENDING, IncidentStatus.AWAITING_REVIEW),
    (IncidentStatus.RUNNING, IncidentStatus.COMPLETED),
    (IncidentStatus.RUNNING, IncidentStatus.PENDING),
    (IncidentStatus.PENDING, IncidentStatus.REJECTED),
    (IncidentStatus.PENDING, IncidentStatus.CONTESTED),
    (IncidentStatus.PENDING, IncidentStatus.FAILED),
    (IncidentStatus.AWAITING_REVIEW, IncidentStatus.RUNNING),
    (IncidentStatus.AWAITING_REVIEW, IncidentStatus.PENDING),
    (IncidentStatus.AWAITING_REVIEW, IncidentStatus.CONTESTED),
    (IncidentStatus.AWAITING_REVIEW, IncidentStatus.FAILED),
)


@pytest.mark.parametrize("current,target", ILLEGAL_SAMPLES)
def test_illegal_transition_returns_err(
    current: IncidentStatus, target: IncidentStatus
) -> None:
    assert (current, target) not in LEGAL_TRANSITIONS  # guard the fixture data
    incident = _incident(current)
    result = transition(incident, target, NOW)
    assert isinstance(result, Err)
    assert isinstance(result.error, StateTransitionError)


@pytest.mark.parametrize("status", ALL_STATUSES)
def test_self_transition_is_always_illegal(status: IncidentStatus) -> None:
    incident = _incident(status)
    result = transition(incident, status, NOW)
    assert isinstance(result, Err)
    assert isinstance(result.error, StateTransitionError)


def test_every_non_legal_pair_is_rejected_by_transition() -> None:
    """Exhaustive: transition() rejects exactly the complement of LEGAL_TRANSITIONS."""
    for current in ALL_STATUSES:
        for target in ALL_STATUSES:
            incident = _incident(current)
            result = transition(incident, target, NOW)
            if (current, target) in LEGAL_TRANSITIONS:
                assert isinstance(result, Ok), (current, target)
            else:
                assert isinstance(result, Err), (current, target)
                assert isinstance(result.error, StateTransitionError), (current, target)


# --- Case 4: no outgoing edge from any terminal status ----------------------


@pytest.mark.parametrize("source", sorted(TERMINAL_INCIDENT_STATUSES))
@pytest.mark.parametrize("target", ALL_STATUSES)
def test_transition_out_of_terminal_status_is_err(
    source: IncidentStatus, target: IncidentStatus
) -> None:
    incident = _incident(source)
    result = transition(incident, target, NOW)
    assert isinstance(result, Err)
    assert isinstance(result.error, StateTransitionError)


@pytest.mark.parametrize("source", sorted(TERMINAL_INCIDENT_STATUSES))
def test_terminal_status_has_no_legal_outgoing_edge(source: IncidentStatus) -> None:
    assert all(src != source for src, _ in LEGAL_TRANSITIONS)
    assert not any(can_transition(source, target) for target in ALL_STATUSES)


# --- Case 5: naive `now` is rejected ----------------------------------------


def test_naive_now_on_legal_edge_is_err() -> None:
    incident = _incident(IncidentStatus.PENDING)
    result = transition(incident, IncidentStatus.RUNNING, NAIVE_NOW)
    assert isinstance(result, Err)
    assert isinstance(result.error, StateTransitionError)


@pytest.mark.parametrize("current,target", sorted(LEGAL_TRANSITIONS))
def test_naive_now_rejected_before_producing_ok(
    current: IncidentStatus, target: IncidentStatus
) -> None:
    incident = _incident(current)
    result = transition(incident, target, NAIVE_NOW)
    assert isinstance(result, Err)


class _NullOffsetTz(tzinfo):
    """A tzinfo whose utcoffset is None -> the datetime is effectively naive."""

    def utcoffset(self, dt: datetime | None) -> timedelta | None:
        return None

    def tzname(self, dt: datetime | None) -> str | None:
        return None

    def dst(self, dt: datetime | None) -> timedelta | None:
        return None


def test_tzinfo_with_none_utcoffset_is_treated_as_naive() -> None:
    incident = _incident(IncidentStatus.PENDING)
    ambiguous = datetime(2026, 7, 7, 12, 0, 0, tzinfo=_NullOffsetTz())
    result = transition(incident, IncidentStatus.RUNNING, ambiguous)
    assert isinstance(result, Err)
    assert isinstance(result.error, StateTransitionError)


def test_non_utc_tz_aware_now_is_accepted() -> None:
    incident = _incident(IncidentStatus.PENDING)
    plus_five = datetime(2026, 7, 7, 17, 30, 0, tzinfo=timezone(timedelta(hours=5)))
    result = transition(incident, IncidentStatus.RUNNING, plus_five)
    assert isinstance(result, Ok)
    assert result.value.updated_at == plus_five


# --- Case 6: AWAITING_REVIEW row is exactly {COMPLETED, REJECTED} -----------


def test_awaiting_review_legal_targets_are_exactly_completed_and_rejected() -> None:
    targets = {
        target
        for source, target in LEGAL_TRANSITIONS
        if source == IncidentStatus.AWAITING_REVIEW
    }
    assert targets == {IncidentStatus.COMPLETED, IncidentStatus.REJECTED}


@pytest.mark.parametrize(
    "target",
    [
        IncidentStatus.RUNNING,
        IncidentStatus.PENDING,
        IncidentStatus.CONTESTED,
        IncidentStatus.FAILED,
        IncidentStatus.AWAITING_REVIEW,
    ],
)
def test_awaiting_review_has_no_auto_or_timeout_edge(target: IncidentStatus) -> None:
    assert not can_transition(IncidentStatus.AWAITING_REVIEW, target)
    incident = _incident(IncidentStatus.AWAITING_REVIEW)
    result = transition(incident, target, NOW)
    assert isinstance(result, Err)


# --- Case 7: reachability -- every non-terminal reaches a terminal ----------


def _reaches_terminal(source: IncidentStatus) -> bool:
    """In-test BFS over LEGAL_TRANSITIONS to a terminal status (>=1 edge)."""
    adjacency: dict[IncidentStatus, set[IncidentStatus]] = defaultdict(set)
    for src, dst in LEGAL_TRANSITIONS:
        adjacency[src].add(dst)

    visited: set[IncidentStatus] = set()
    frontier: list[IncidentStatus] = list(adjacency[source])
    while frontier:
        node = frontier.pop()
        if node in visited:
            continue
        visited.add(node)
        if node in TERMINAL_INCIDENT_STATUSES:
            return True
        frontier.extend(adjacency[node])
    return False


@pytest.mark.parametrize("source", sorted(NON_TERMINAL_INCIDENT_STATUSES))
def test_every_non_terminal_status_reaches_a_terminal(source: IncidentStatus) -> None:
    assert _reaches_terminal(source)


def test_reachability_bfs_rejects_a_graph_with_a_trapped_state() -> None:
    """Guard the BFS helper itself: an isolated non-terminal must not 'reach'."""
    trapped = IncidentStatus.PENDING
    isolated_edges = frozenset(
        edge for edge in LEGAL_TRANSITIONS if edge[0] != trapped
    )
    # Re-run the closure against a graph with PENDING's outgoing edges removed.
    adjacency: dict[IncidentStatus, set[IncidentStatus]] = defaultdict(set)
    for src, dst in isolated_edges:
        adjacency[src].add(dst)
    visited: set[IncidentStatus] = set()
    frontier: list[IncidentStatus] = list(adjacency[trapped])
    reached = False
    while frontier:
        node = frontier.pop()
        if node in visited:
            continue
        visited.add(node)
        if node in TERMINAL_INCIDENT_STATUSES:
            reached = True
            break
        frontier.extend(adjacency[node])
    assert reached is False


# --- Case 8: is_terminal matches TERMINAL_INCIDENT_STATUSES exactly ---------


@pytest.mark.parametrize("status", ALL_STATUSES)
def test_is_terminal_matches_terminal_set(status: IncidentStatus) -> None:
    assert is_terminal(status) == (status in TERMINAL_INCIDENT_STATUSES)


def test_terminal_and_non_terminal_partition_covers_all_statuses() -> None:
    assert (
        TERMINAL_INCIDENT_STATUSES | NON_TERMINAL_INCIDENT_STATUSES
    ) == frozenset(ALL_STATUSES)
    assert (
        TERMINAL_INCIDENT_STATUSES & NON_TERMINAL_INCIDENT_STATUSES
    ) == frozenset()
