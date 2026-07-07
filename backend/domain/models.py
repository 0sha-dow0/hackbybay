from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator

from backend.domain.constants import (
    CONSENSUS_APPROVALS_REQUIRED,
    CONSENSUS_PANEL_SIZE,
    DEGRADED_APPROVALS_REQUIRED,
    DEGRADED_PANEL_SIZE,
)
from backend.domain.enums import (
    TERMINAL_PIPELINE_STAGES,
    FileDecisionKind,
    GraphEdgeKind,
    GraphNodeKind,
    IncidentStatus,
    JudgeName,
    PipelineStage,
    ReviewDecision,
    SandboxOutcome,
    StrategyKind,
    TriggerType,
    Verdict,
)

type JsonValue = (
    str
    | int
    | float
    | bool
    | None
    | Mapping[str, JsonValue]
    | Sequence[JsonValue]
)


def _required_approvals(panel_size: int) -> int:
    if panel_size == CONSENSUS_PANEL_SIZE:
        return CONSENSUS_APPROVALS_REQUIRED
    if panel_size == DEGRADED_PANEL_SIZE:
        return DEGRADED_APPROVALS_REQUIRED
    raise ValueError(
        f"panel_size {panel_size!r} is not one of the supported panel sizes "
        f"({DEGRADED_PANEL_SIZE}, {CONSENSUS_PANEL_SIZE})"
    )


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Repo(_FrozenModel):
    id: str
    url: str
    owner: str
    registered_at: datetime


class GraphNode(_FrozenModel):
    id: str
    kind: GraphNodeKind
    label: str
    attrs: Mapping[str, str]


class GraphEdge(_FrozenModel):
    src: str
    dst: str
    kind: GraphEdgeKind


class CallSite(_FrozenModel):
    file_path: str
    line: int
    symbol: str
    is_aliased: bool
    alias: str | None
    snippet: str


class SurgeryPlan(_FrozenModel):
    target_package: str
    call_sites: tuple[CallSite, ...]
    affected_files: tuple[str, ...]

    @model_validator(mode="after")
    def _validate_affected_files(self) -> Self:
        expected = tuple(sorted({call_site.file_path for call_site in self.call_sites}))
        if self.affected_files != expected:
            raise ValueError(
                "affected_files must equal the sorted unique call-site file paths "
                f"{expected!r}, received {self.affected_files!r}"
            )
        return self


class CentralityScore(_FrozenModel):
    package: str
    score: float


class GraphLayoutNode(_FrozenModel):
    id: str
    x: float
    y: float
    kind: GraphNodeKind
    label: str


class GraphLayout(_FrozenModel):
    nodes: tuple[GraphLayoutNode, ...]
    edges: tuple[GraphEdge, ...]


class FileContent(_FrozenModel):
    path: str
    text: str


class LockfileWarning(_FrozenModel):
    shape: str
    reason: str


class UnderwritingReport(_FrozenModel):
    id: str
    repo_id: str
    target_package: str
    failing_tests: tuple[str, ...]
    affected_file_count: int
    centrality: tuple[CentralityScore, ...]
    graph_layout: GraphLayout
    warnings: tuple[LockfileWarning, ...]
    created_at: datetime


class MitigationOption(_FrozenModel):
    kind: StrategyKind
    title: str
    effort: str
    blast_radius: str
    residual_risk: str
    executable: bool
    rationale: str


class MitigationCardSet(_FrozenModel):
    incident_id: str
    options: tuple[MitigationOption, ...]


class Incident(_FrozenModel):
    id: str
    repo_id: str
    trigger_type: TriggerType
    chosen_strategy: StrategyKind | None
    status: IncidentStatus
    created_at: datetime
    updated_at: datetime


class Recipe(_FrozenModel):
    id: str
    library_pair: str
    wrapper_pattern: str
    known_gaps: tuple[str, ...]
    confirmed_fix: str


class TransplantRequest(_FrozenModel):
    incident_id: str
    surgery_plan: SurgeryPlan
    files: tuple[FileContent, ...]
    recipe: Recipe | None


class RewrittenFile(_FrozenModel):
    path: str
    text: str


class TransplantOutput(_FrozenModel):
    attempt: int
    files: tuple[RewrittenFile, ...]
    raw_model_text: str


class FileDiff(_FrozenModel):
    path: str
    unified_diff: str
    before: str
    after: str


class BehavioralCase(_FrozenModel):
    id: str
    description: str
    request: Mapping[str, JsonValue]
    category: str


class NormalizedOutput(_FrozenModel):
    case_id: str
    normalized: str


class BehavioralCaseResult(_FrozenModel):
    case_id: str
    golden: NormalizedOutput
    candidate: NormalizedOutput
    equal: bool


class BehavioralDiffResult(_FrozenModel):
    matched: bool
    per_case: tuple[BehavioralCaseResult, ...]


class BuildResult(_FrozenModel):
    outcome: SandboxOutcome
    log: str


class TestResult(_FrozenModel):
    outcome: SandboxOutcome
    failing_tests: tuple[str, ...]
    log: str


class EvidenceBundle(_FrozenModel):
    transplant_id: str
    diff: tuple[FileDiff, ...]
    build: BuildResult
    test: TestResult
    behavioral: BehavioralDiffResult


class SanitizedEvidence(_FrozenModel):
    transplant_id: str
    diff_text: str
    build_summary: str
    test_summary: str
    behavioral_summary: str


class JudgeVerdict(_FrozenModel):
    transplant_id: str
    judge_name: JudgeName
    verdict: Verdict
    rationale: str


class ConsensusResult(_FrozenModel):
    approvals: int
    panel_size: int
    approved: bool
    contested: bool
    verdicts: tuple[JudgeVerdict, ...]

    @model_validator(mode="after")
    def _validate_consensus(self) -> Self:
        required = _required_approvals(self.panel_size)
        if not 0 <= self.approvals <= self.panel_size:
            raise ValueError(
                f"approvals {self.approvals!r} must lie within [0, {self.panel_size}]"
            )
        if self.approved != (self.approvals >= required):
            raise ValueError(
                f"approved {self.approved!r} must equal (approvals >= {required})"
            )
        if self.contested != (not self.approved):
            raise ValueError(
                f"contested {self.contested!r} must equal (not approved) "
                f"for approved {self.approved!r}"
            )
        return self


class Transplant(_FrozenModel):
    id: str
    incident_id: str
    surgery_plan: SurgeryPlan
    diff: tuple[FileDiff, ...]
    evidence: EvidenceBundle
    consensus: ConsensusResult


class FileDecision(_FrozenModel):
    path: str
    kind: FileDecisionKind
    reason: str | None


class Review(_FrozenModel):
    transplant_id: str
    user_id: str
    decision: ReviewDecision
    per_file: tuple[FileDecision, ...]
    reason: str | None


class PullRequestRef(_FrozenModel):
    number: int
    url: str


class PipelineEvent(_FrozenModel):
    incident_id: str
    stage: PipelineStage
    seq: int
    message: str
    at: datetime
    terminal: bool

    @model_validator(mode="after")
    def _validate_pipeline_event(self) -> Self:
        if self.seq < 0:
            raise ValueError(f"seq {self.seq!r} must be non-negative")
        if self.terminal != (self.stage in TERMINAL_PIPELINE_STAGES):
            raise ValueError(
                f"terminal {self.terminal!r} must equal (stage in terminal stages) "
                f"for stage {self.stage!r}"
            )
        return self


__all__ = (
    "JsonValue",
    "Repo",
    "GraphNode",
    "GraphEdge",
    "CallSite",
    "SurgeryPlan",
    "CentralityScore",
    "GraphLayoutNode",
    "GraphLayout",
    "FileContent",
    "LockfileWarning",
    "UnderwritingReport",
    "MitigationOption",
    "MitigationCardSet",
    "Incident",
    "Recipe",
    "TransplantRequest",
    "RewrittenFile",
    "TransplantOutput",
    "FileDiff",
    "BehavioralCase",
    "NormalizedOutput",
    "BehavioralCaseResult",
    "BehavioralDiffResult",
    "BuildResult",
    "TestResult",
    "EvidenceBundle",
    "SanitizedEvidence",
    "JudgeVerdict",
    "ConsensusResult",
    "Transplant",
    "FileDecision",
    "Review",
    "PullRequestRef",
    "PipelineEvent",
)
