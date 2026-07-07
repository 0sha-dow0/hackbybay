"""Tests for backend.domain.models (Unit 2: Domain models).

Verifies the frozen/extra-forbid pydantic contract, type validation,
round-trip identity across simple and deeply nested models, and the three
cross-field ``model_validator`` invariants (SurgeryPlan.affected_files,
ConsensusResult consensus arithmetic, PipelineEvent terminal/seq).

Constants that parametrise the consensus panel are imported rather than
hard-coded so the tests track the published contract.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import BaseModel, ValidationError

from backend.domain.constants import (
    CONSENSUS_APPROVALS_REQUIRED,
    CONSENSUS_PANEL_SIZE,
    DEGRADED_APPROVALS_REQUIRED,
    DEGRADED_PANEL_SIZE,
)
from backend.domain.enums import (
    GraphNodeKind,
    JudgeName,
    PipelineStage,
    SandboxOutcome,
    TERMINAL_PIPELINE_STAGES,
    Verdict,
)
from backend.domain.models import (
    BehavioralCase,
    BehavioralCaseResult,
    BehavioralDiffResult,
    BuildResult,
    CallSite,
    ConsensusResult,
    EvidenceBundle,
    FileDiff,
    GraphNode,
    JudgeVerdict,
    NormalizedOutput,
    PipelineEvent,
    Repo,
    SurgeryPlan,
    Transplant,
)
from backend.domain.models import TestResult as SandboxTestResult

FIXED_DT: datetime = datetime(2026, 7, 7, 12, 0, 0, tzinfo=timezone.utc)


# --- Builders for valid representative instances ---------------------------


def _repo() -> Repo:
    return Repo(id="r1", url="https://example/r", owner="o", registered_at=FIXED_DT)


def _graph_node() -> GraphNode:
    return GraphNode(
        id="n1",
        kind=GraphNodeKind.PACKAGE,
        label="axios",
        attrs={"a": "1", "b": "2"},
    )


def _call_site(file_path: str, line: int) -> CallSite:
    return CallSite(
        file_path=file_path,
        line=line,
        symbol="get",
        is_aliased=False,
        alias=None,
        snippet="axios.get()",
    )


def _surgery_plan() -> SurgeryPlan:
    """Two call sites in unsorted, duplicate-inducing order."""
    return SurgeryPlan(
        target_package="axios",
        call_sites=(
            _call_site("src/b.js", 10),
            _call_site("src/a.js", 3),
            _call_site("src/b.js", 42),
        ),
        affected_files=("src/a.js", "src/b.js"),
    )


def _empty_surgery_plan() -> SurgeryPlan:
    return SurgeryPlan(target_package="axios", call_sites=(), affected_files=())


def _consensus() -> ConsensusResult:
    return ConsensusResult(
        approvals=CONSENSUS_APPROVALS_REQUIRED,
        panel_size=CONSENSUS_PANEL_SIZE,
        approved=True,
        contested=False,
        verdicts=(
            JudgeVerdict(
                transplant_id="t1",
                judge_name=JudgeName.CORRECTNESS,
                verdict=Verdict.APPROVE,
                rationale="ok",
            ),
        ),
    )


def _pipeline_event() -> PipelineEvent:
    return PipelineEvent(
        incident_id="i1",
        stage=PipelineStage.COMPLETED,
        seq=7,
        message="done",
        at=FIXED_DT,
        terminal=True,
    )


def _behavioral_case() -> BehavioralCase:
    return BehavioralCase(
        id="c1",
        description="nested request",
        request={
            "method": "GET",
            "retries": 3,
            "timeout": 1.5,
            "verify": True,
            "body": None,
            "headers": {"x": "1", "nested": {"deep": [1, 2, 3]}},
            "list": [1, "two", {"k": "v"}, [True, None]],
        },
        category="http",
    )


def _evidence_bundle() -> EvidenceBundle:
    return EvidenceBundle(
        transplant_id="t1",
        diff=(FileDiff(path="src/a.js", unified_diff="@@ -1 +1 @@", before="b", after="a"),),
        build=BuildResult(outcome=SandboxOutcome.PASSED, log="built"),
        test=SandboxTestResult(outcome=SandboxOutcome.PASSED, failing_tests=(), log="tests ok"),
        behavioral=BehavioralDiffResult(
            matched=True,
            per_case=(
                BehavioralCaseResult(
                    case_id="c1",
                    golden=NormalizedOutput(case_id="c1", normalized="g"),
                    candidate=NormalizedOutput(case_id="c1", normalized="g"),
                    equal=True,
                ),
            ),
        ),
    )


def _transplant() -> Transplant:
    return Transplant(
        id="t1",
        incident_id="i1",
        surgery_plan=_surgery_plan(),
        diff=(FileDiff(path="src/a.js", unified_diff="@@ -1 +1 @@", before="b", after="a"),),
        evidence=_evidence_bundle(),
        consensus=_consensus(),
    )


def _all_sample_models() -> list[BaseModel]:
    return [
        _repo(),
        _graph_node(),
        _call_site("src/a.js", 1),
        _surgery_plan(),
        _empty_surgery_plan(),
        _consensus(),
        _pipeline_event(),
        _behavioral_case(),
        _evidence_bundle(),
        _transplant(),
    ]


# --- Criterion 1: round-trip identity --------------------------------------


def test_round_trip_identity_for_representative_models() -> None:
    for model in _all_sample_models():
        restored = type(model).model_validate(model.model_dump())
        assert restored == model, (
            f"{type(model).__name__} round-trip mismatch: "
            f"model_validate(model_dump()) == {restored!r}, expected {model!r}"
        )


def test_round_trip_identity_json_mode() -> None:
    # Serialising through JSON-native primitives (datetime->str, tuple->list)
    # and back must still reconstruct an equal model.
    for model in _all_sample_models():
        restored = type(model).model_validate(model.model_dump(mode="json"))
        assert restored == model, (
            f"{type(model).__name__} JSON round-trip mismatch: "
            f"got {restored!r}, expected {model!r}"
        )


def test_nested_json_value_mapping_preserved_exactly() -> None:
    case = _behavioral_case()
    dumped = case.model_dump()
    assert dumped["request"] == case.request, (
        f"request mapping altered by dump: {dumped['request']!r} != {case.request!r}"
    )
    restored = BehavioralCase.model_validate(dumped)
    assert restored.request == case.request


# --- Criterion 2: extra="forbid" -------------------------------------------


def test_unknown_extra_key_is_rejected_repo() -> None:
    with pytest.raises(ValidationError):
        Repo(
            id="r1",
            url="u",
            owner="o",
            registered_at=FIXED_DT,
            surprise="x",  # type: ignore[call-arg]
        )


def test_unknown_extra_key_is_rejected_call_site() -> None:
    with pytest.raises(ValidationError):
        CallSite(
            file_path="a.js",
            line=1,
            symbol="get",
            is_aliased=False,
            alias=None,
            snippet="s",
            extra_field=1,  # type: ignore[call-arg]
        )


def test_unknown_extra_key_is_rejected_consensus() -> None:
    with pytest.raises(ValidationError):
        ConsensusResult(
            approvals=CONSENSUS_APPROVALS_REQUIRED,
            panel_size=CONSENSUS_PANEL_SIZE,
            approved=True,
            contested=False,
            verdicts=(),
            bogus=True,  # type: ignore[call-arg]
        )


# --- Criterion 3: type validation ------------------------------------------


def test_wrong_typed_field_rejected_callsite_line() -> None:
    with pytest.raises(ValidationError):
        CallSite(
            file_path="a.js",
            line="x",  # type: ignore[arg-type]
            symbol="get",
            is_aliased=False,
            alias=None,
            snippet="s",
        )


def test_wrong_typed_field_rejected_repo_datetime() -> None:
    with pytest.raises(ValidationError):
        Repo(
            id="r1",
            url="u",
            owner="o",
            registered_at="not-a-datetime",  # type: ignore[arg-type]
        )


def test_wrong_typed_enum_value_rejected() -> None:
    with pytest.raises(ValidationError):
        GraphNode(
            id="n1",
            kind="not_a_kind",  # type: ignore[arg-type]
            label="l",
            attrs={},
        )


# --- Criterion 4: frozen ----------------------------------------------------


def test_field_mutation_after_construction_raises() -> None:
    repo = _repo()
    with pytest.raises((ValidationError, TypeError)):
        repo.id = "mutated"
    # The failed assignment must not have altered the instance.
    assert repo.id == "r1", f"frozen model was mutated: id == {repo.id!r}"


def test_frozen_model_is_hashable() -> None:
    # Frozen pydantic models are hashable; identical models hash equally.
    assert hash(_repo()) == hash(_repo())
    assert hash(_surgery_plan()) == hash(_surgery_plan())


# --- Criterion 5: SurgeryPlan validator ------------------------------------


def test_surgery_plan_zero_call_sites_valid() -> None:
    plan = _empty_surgery_plan()
    assert plan.call_sites == ()
    assert plan.affected_files == ()


def test_surgery_plan_consistent_affected_files_sorted_and_deduped() -> None:
    plan = _surgery_plan()
    expected = tuple(sorted({cs.file_path for cs in plan.call_sites}))
    assert plan.affected_files == expected, (
        f"affected_files == {plan.affected_files!r}, expected sorted-unique {expected!r}"
    )
    # Explicitly confirm sortedness and de-duplication.
    assert list(plan.affected_files) == sorted(plan.affected_files)
    assert len(set(plan.affected_files)) == len(plan.affected_files)


def test_surgery_plan_wrong_order_affected_files_rejected() -> None:
    # call sites -> {src/a.js, src/b.js}; sorted-unique is (a, b). Reversed fails.
    with pytest.raises(ValidationError):
        SurgeryPlan(
            target_package="axios",
            call_sites=(_call_site("src/b.js", 1), _call_site("src/a.js", 2)),
            affected_files=("src/b.js", "src/a.js"),
        )


def test_surgery_plan_non_deduped_affected_files_rejected() -> None:
    with pytest.raises(ValidationError):
        SurgeryPlan(
            target_package="axios",
            call_sites=(_call_site("src/a.js", 1), _call_site("src/a.js", 2)),
            affected_files=("src/a.js", "src/a.js"),
        )


def test_surgery_plan_affected_files_without_call_sites_rejected() -> None:
    with pytest.raises(ValidationError):
        SurgeryPlan(
            target_package="axios",
            call_sites=(),
            affected_files=("src/a.js",),
        )


# --- Criterion 6: ConsensusResult validator --------------------------------


def _consensus_kwargs(
    approvals: int,
    panel_size: int,
    approved: bool,
    contested: bool,
) -> ConsensusResult:
    return ConsensusResult(
        approvals=approvals,
        panel_size=panel_size,
        approved=approved,
        contested=contested,
        verdicts=(),
    )


def test_consensus_full_panel_meeting_threshold_approved() -> None:
    result = _consensus_kwargs(
        approvals=CONSENSUS_APPROVALS_REQUIRED,
        panel_size=CONSENSUS_PANEL_SIZE,
        approved=True,
        contested=False,
    )
    assert result.approved is True
    assert result.contested is False


def test_consensus_below_threshold_must_not_be_approved() -> None:
    below = CONSENSUS_APPROVALS_REQUIRED - 1
    # approved=True with insufficient approvals is contradictory -> rejected.
    with pytest.raises(ValidationError):
        _consensus_kwargs(
            approvals=below,
            panel_size=CONSENSUS_PANEL_SIZE,
            approved=True,
            contested=False,
        )
    # The consistent form (not approved, contested) is valid.
    valid = _consensus_kwargs(
        approvals=below,
        panel_size=CONSENSUS_PANEL_SIZE,
        approved=False,
        contested=True,
    )
    assert valid.approved is False
    assert valid.contested is True


def test_consensus_approved_and_contested_mutually_exclusive() -> None:
    with pytest.raises(ValidationError):
        _consensus_kwargs(
            approvals=CONSENSUS_APPROVALS_REQUIRED,
            panel_size=CONSENSUS_PANEL_SIZE,
            approved=True,
            contested=True,
        )


def test_consensus_panel_size_zero_rejected() -> None:
    with pytest.raises(ValidationError):
        _consensus_kwargs(approvals=0, panel_size=0, approved=False, contested=True)


def test_consensus_unknown_panel_size_rejected() -> None:
    unknown = CONSENSUS_PANEL_SIZE - 1  # 3: neither full nor degraded
    assert unknown not in (CONSENSUS_PANEL_SIZE, DEGRADED_PANEL_SIZE)
    with pytest.raises(ValidationError):
        _consensus_kwargs(
            approvals=unknown,
            panel_size=unknown,
            approved=True,
            contested=False,
        )


def test_consensus_approvals_exceeding_panel_rejected() -> None:
    with pytest.raises(ValidationError):
        _consensus_kwargs(
            approvals=CONSENSUS_PANEL_SIZE + 1,
            panel_size=CONSENSUS_PANEL_SIZE,
            approved=True,
            contested=False,
        )


def test_consensus_negative_approvals_rejected() -> None:
    with pytest.raises(ValidationError):
        _consensus_kwargs(
            approvals=-1,
            panel_size=CONSENSUS_PANEL_SIZE,
            approved=False,
            contested=True,
        )


def test_consensus_degraded_panel_meeting_threshold_approved() -> None:
    result = _consensus_kwargs(
        approvals=DEGRADED_APPROVALS_REQUIRED,
        panel_size=DEGRADED_PANEL_SIZE,
        approved=True,
        contested=False,
    )
    assert result.approved is True
    assert result.contested is False


def test_consensus_degraded_panel_below_threshold_contested() -> None:
    below = DEGRADED_APPROVALS_REQUIRED - 1
    result = _consensus_kwargs(
        approvals=below,
        panel_size=DEGRADED_PANEL_SIZE,
        approved=False,
        contested=True,
    )
    assert result.approved is False
    assert result.contested is True


# --- Criterion 7: PipelineEvent validator ----------------------------------


def _pipeline(stage: PipelineStage, seq: int, terminal: bool) -> PipelineEvent:
    return PipelineEvent(
        incident_id="i1",
        stage=stage,
        seq=seq,
        message="m",
        at=FIXED_DT,
        terminal=terminal,
    )


def test_pipeline_terminal_stage_marked_terminal_valid() -> None:
    assert PipelineStage.COMPLETED in TERMINAL_PIPELINE_STAGES
    event = _pipeline(PipelineStage.COMPLETED, seq=1, terminal=True)
    assert event.terminal is True


def test_pipeline_non_terminal_stage_marked_non_terminal_valid() -> None:
    assert PipelineStage.REWRITE not in TERMINAL_PIPELINE_STAGES
    event = _pipeline(PipelineStage.REWRITE, seq=0, terminal=False)
    assert event.terminal is False


def test_pipeline_non_terminal_stage_marked_terminal_rejected() -> None:
    with pytest.raises(ValidationError):
        _pipeline(PipelineStage.REWRITE, seq=1, terminal=True)


def test_pipeline_terminal_stage_marked_non_terminal_rejected() -> None:
    with pytest.raises(ValidationError):
        _pipeline(PipelineStage.COMPLETED, seq=1, terminal=False)


def test_pipeline_negative_seq_rejected() -> None:
    with pytest.raises(ValidationError):
        _pipeline(PipelineStage.REWRITE, seq=-1, terminal=False)


def test_pipeline_zero_seq_boundary_valid() -> None:
    event = _pipeline(PipelineStage.REWRITE, seq=0, terminal=False)
    assert event.seq == 0


# --- Criterion 8: empty-collection semantics -------------------------------


def test_behavioral_diff_result_empty_per_case_valid() -> None:
    result = BehavioralDiffResult(matched=False, per_case=())
    assert result.per_case == ()
    assert result.matched is False


def test_consensus_empty_verdicts_valid() -> None:
    result = _consensus_kwargs(
        approvals=CONSENSUS_APPROVALS_REQUIRED,
        panel_size=CONSENSUS_PANEL_SIZE,
        approved=True,
        contested=False,
    )
    assert result.verdicts == ()
