"""Tests for backend.domain.enums.

Verifies the public contract of every StrEnum and the three partition
frozensets: snake_case member values, str-subclass identity, exact member
membership, the IncidentStatus terminal/non-terminal partition, the
TERMINAL_PIPELINE_STAGES subset relation, rejection of invalid values, and
idempotent (deterministic) import.
"""

from __future__ import annotations

from enum import StrEnum
from importlib import reload

import pytest

import backend.domain.enums as enums_module
from backend.domain.enums import (
    FileDecisionKind,
    GraphEdgeKind,
    GraphNodeKind,
    IncidentStatus,
    JudgeName,
    LlmRole,
    NON_TERMINAL_INCIDENT_STATUSES,
    PipelineStage,
    ReviewDecision,
    SandboxOutcome,
    StrategyKind,
    TERMINAL_INCIDENT_STATUSES,
    TERMINAL_PIPELINE_STAGES,
    TriggerType,
    Verdict,
)

# --- Contract tables -------------------------------------------------------

# Exact name -> value mapping for every enum, taken verbatim from the
# published contract. This guards against both wrong values and unexpected
# extra / missing members (acceptance criteria 1 and 7).
EXPECTED_ENUM_MEMBERS: dict[type[StrEnum], dict[str, str]] = {
    IncidentStatus: {
        "PENDING": "pending",
        "RUNNING": "running",
        "AWAITING_REVIEW": "awaiting_review",
        "COMPLETED": "completed",
        "REJECTED": "rejected",
        "CONTESTED": "contested",
        "FAILED": "failed",
    },
    TriggerType: {
        "MOCK_CVE": "mock_cve",
        "PR_GATE": "pr_gate",
    },
    StrategyKind: {
        "UPGRADE": "upgrade",
        "SHIM": "shim",
        "TRANSPLANT": "transplant",
        "ACCEPT_RISK": "accept_risk",
    },
    PipelineStage: {
        "RECALL": "recall",
        "REWRITE": "rewrite",
        "VALIDATE": "validate",
        "VERIFY_BUILD": "verify_build",
        "VERIFY_TEST": "verify_test",
        "VERIFY_BEHAVIORAL": "verify_behavioral",
        "JUDGE": "judge",
        "AWAITING_REVIEW": "awaiting_review",
        "COMPLETED": "completed",
        "CONTESTED": "contested",
        "FAILED": "failed",
    },
    JudgeName: {
        "CORRECTNESS": "correctness",
        "SECURITY": "security",
        "MINIMALITY": "minimality",
        "RECIPE_FIDELITY": "recipe_fidelity",
    },
    Verdict: {
        "APPROVE": "approve",
        "REJECT": "reject",
    },
    ReviewDecision: {
        "ACCEPT_ALL": "accept_all",
        "REJECT": "reject",
    },
    FileDecisionKind: {
        "ACCEPT": "accept",
        "REJECT": "reject",
    },
    SandboxOutcome: {
        "PASSED": "passed",
        "FAILED": "failed",
        "TIMEOUT": "timeout",
        "ERROR": "error",
    },
    LlmRole: {
        "TRANSPLANT": "transplant",
        "JUDGE_CORRECTNESS": "judge_correctness",
        "JUDGE_SECURITY": "judge_security",
        "JUDGE_MINIMALITY": "judge_minimality",
        "JUDGE_RECIPE": "judge_recipe",
        "MITIGATION": "mitigation",
        "PR_SCREEN": "pr_screen",
    },
    GraphNodeKind: {
        "PACKAGE": "package",
        "FILE": "file",
        "CALL_SITE": "call_site",
    },
    GraphEdgeKind: {
        "DEPENDS_ON": "depends_on",
        "IMPORTS": "imports",
        "CALLS": "calls",
    },
}

ALL_ENUMS: tuple[type[StrEnum], ...] = tuple(EXPECTED_ENUM_MEMBERS.keys())

# A value that must never be a legitimate member of any enum under test.
BOGUS_VALUE: str = "__definitely_not_a_valid_member__"


def _member_value(member: StrEnum) -> str:
    """Return the string value of a StrEnum member as a concrete ``str``.

    ``Enum.value`` is typed as ``Any`` in typeshed; for a ``StrEnum`` the
    member *is* its value string, so ``str(member)`` yields it losslessly.
    """
    return str(member)


# --- Acceptance criterion 1: value == snake_case(name) ---------------------


def test_every_member_value_is_snake_case_of_name() -> None:
    for enum_cls in ALL_ENUMS:
        for member in enum_cls:
            expected = member.name.lower()
            actual = _member_value(member)
            assert actual == expected, (
                f"{enum_cls.__name__}.{member.name}.value == {actual!r}, "
                f"expected snake_case(name) == {expected!r}"
            )
            # Access via the documented ``.value`` attribute too.
            assert member.value == expected, (
                f"{enum_cls.__name__}.{member.name}.value == "
                f"{member.value!r}, expected {expected!r}"
            )


def test_documented_example_awaiting_review() -> None:
    assert IncidentStatus.AWAITING_REVIEW.value == "awaiting_review"


# --- Acceptance criteria 1 & 7: exact members and values -------------------


def test_member_sets_match_contract_exactly() -> None:
    for enum_cls, expected in EXPECTED_ENUM_MEMBERS.items():
        actual_names = {member.name for member in enum_cls}
        expected_names = set(expected.keys())
        missing = expected_names - actual_names
        extra = actual_names - expected_names
        assert not missing, f"{enum_cls.__name__} missing members: {missing}"
        assert not extra, f"{enum_cls.__name__} unexpected members: {extra}"
        for name, value in expected.items():
            member = enum_cls[name]
            assert _member_value(member) == value, (
                f"{enum_cls.__name__}.{name} == "
                f"{_member_value(member)!r}, expected {value!r}"
            )


# --- Acceptance criterion 2: StrEnum / str subclass identity ---------------


def test_every_enum_is_str_subclass() -> None:
    for enum_cls in ALL_ENUMS:
        assert issubclass(enum_cls, str), (
            f"{enum_cls.__name__} is not a str subclass"
        )
        assert issubclass(enum_cls, StrEnum), (
            f"{enum_cls.__name__} is not a StrEnum"
        )


def test_members_compare_equal_to_their_string_value() -> None:
    for enum_cls in ALL_ENUMS:
        for member in enum_cls:
            assert isinstance(member, str), (
                f"{enum_cls.__name__}.{member.name} is not a str instance"
            )
            assert member == member.value, (
                f"{enum_cls.__name__}.{member.name} != its own .value"
            )
            assert member == _member_value(member), (
                f"{enum_cls.__name__}.{member.name} != its string value"
            )


# --- Acceptance criterion 3: IncidentStatus partition ----------------------


def test_terminal_incident_statuses_exact_contents() -> None:
    expected = {
        IncidentStatus.COMPLETED,
        IncidentStatus.REJECTED,
        IncidentStatus.CONTESTED,
        IncidentStatus.FAILED,
    }
    assert set(TERMINAL_INCIDENT_STATUSES) == expected


def test_non_terminal_incident_statuses_exact_contents() -> None:
    expected = {
        IncidentStatus.PENDING,
        IncidentStatus.RUNNING,
        IncidentStatus.AWAITING_REVIEW,
    }
    assert set(NON_TERMINAL_INCIDENT_STATUSES) == expected


def test_incident_status_partition_is_complete_and_disjoint() -> None:
    union = set(TERMINAL_INCIDENT_STATUSES) | set(NON_TERMINAL_INCIDENT_STATUSES)
    assert union == set(IncidentStatus), (
        f"partition union {union} != all members {set(IncidentStatus)}"
    )
    intersection = set(TERMINAL_INCIDENT_STATUSES) & set(
        NON_TERMINAL_INCIDENT_STATUSES
    )
    assert intersection == set(), (
        f"terminal and non-terminal overlap: {intersection}"
    )


# --- Acceptance criterion 4: TERMINAL_PIPELINE_STAGES subset ---------------


def test_terminal_pipeline_stages_exact_contents() -> None:
    expected = {
        PipelineStage.AWAITING_REVIEW,
        PipelineStage.COMPLETED,
        PipelineStage.CONTESTED,
        PipelineStage.FAILED,
    }
    assert set(TERMINAL_PIPELINE_STAGES) == expected


def test_terminal_pipeline_stages_is_subset_of_pipeline_stage() -> None:
    assert set(TERMINAL_PIPELINE_STAGES) <= set(PipelineStage), (
        f"terminal stages {set(TERMINAL_PIPELINE_STAGES)} not a subset of "
        f"{set(PipelineStage)}"
    )


# --- Acceptance criterion 7: invalid construction rejected -----------------


def test_constructing_from_invalid_string_raises_value_error() -> None:
    for enum_cls in ALL_ENUMS:
        with pytest.raises(ValueError):
            enum_cls(BOGUS_VALUE)


def test_uppercase_name_is_not_a_valid_value() -> None:
    # Members are looked up by value (snake_case), never by their NAME.
    with pytest.raises(ValueError):
        IncidentStatus("AWAITING_REVIEW")


def test_empty_string_is_not_a_valid_value() -> None:
    for enum_cls in ALL_ENUMS:
        with pytest.raises(ValueError):
            enum_cls("")


# --- Acceptance criterion 8: deterministic / idempotent import -------------


def _enum_signature() -> dict[str, frozenset[tuple[str, str]]]:
    """Snapshot every enum in the (possibly reloaded) module by name/value."""
    classes: tuple[type[StrEnum], ...] = (
        enums_module.IncidentStatus,
        enums_module.TriggerType,
        enums_module.StrategyKind,
        enums_module.PipelineStage,
        enums_module.JudgeName,
        enums_module.Verdict,
        enums_module.ReviewDecision,
        enums_module.FileDecisionKind,
        enums_module.SandboxOutcome,
        enums_module.LlmRole,
        enums_module.GraphNodeKind,
        enums_module.GraphEdgeKind,
    )
    return {
        cls.__name__: frozenset(
            (member.name, str(member)) for member in cls
        )
        for cls in classes
    }


def _frozenset_signature() -> dict[str, frozenset[str]]:
    return {
        "TERMINAL_INCIDENT_STATUSES": frozenset(
            str(status) for status in enums_module.TERMINAL_INCIDENT_STATUSES
        ),
        "NON_TERMINAL_INCIDENT_STATUSES": frozenset(
            str(status)
            for status in enums_module.NON_TERMINAL_INCIDENT_STATUSES
        ),
        "TERMINAL_PIPELINE_STAGES": frozenset(
            str(stage) for stage in enums_module.TERMINAL_PIPELINE_STAGES
        ),
    }


def test_reimport_is_idempotent() -> None:
    enums_before = _enum_signature()
    frozensets_before = _frozenset_signature()
    reload(enums_module)
    enums_after = _enum_signature()
    frozensets_after = _frozenset_signature()
    assert enums_after == enums_before, (
        "enum member sets changed after re-import"
    )
    assert frozensets_after == frozensets_before, (
        "partition frozensets changed after re-import"
    )
