from enum import StrEnum


class IncidentStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_REVIEW = "awaiting_review"
    COMPLETED = "completed"
    REJECTED = "rejected"
    CONTESTED = "contested"
    FAILED = "failed"


class TriggerType(StrEnum):
    MOCK_CVE = "mock_cve"
    PR_GATE = "pr_gate"


class StrategyKind(StrEnum):
    UPGRADE = "upgrade"
    SHIM = "shim"
    TRANSPLANT = "transplant"
    ACCEPT_RISK = "accept_risk"


class PipelineStage(StrEnum):
    RECALL = "recall"
    REWRITE = "rewrite"
    VALIDATE = "validate"
    VERIFY_BUILD = "verify_build"
    VERIFY_TEST = "verify_test"
    VERIFY_BEHAVIORAL = "verify_behavioral"
    JUDGE = "judge"
    AWAITING_REVIEW = "awaiting_review"
    COMPLETED = "completed"
    CONTESTED = "contested"
    FAILED = "failed"


class JudgeName(StrEnum):
    CORRECTNESS = "correctness"
    SECURITY = "security"
    MINIMALITY = "minimality"
    RECIPE_FIDELITY = "recipe_fidelity"


class Verdict(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class ReviewDecision(StrEnum):
    ACCEPT_ALL = "accept_all"
    REJECT = "reject"


class FileDecisionKind(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"


class SandboxOutcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    ERROR = "error"


class LlmRole(StrEnum):
    TRANSPLANT = "transplant"
    JUDGE_CORRECTNESS = "judge_correctness"
    JUDGE_SECURITY = "judge_security"
    JUDGE_MINIMALITY = "judge_minimality"
    JUDGE_RECIPE = "judge_recipe"
    MITIGATION = "mitigation"
    PR_SCREEN = "pr_screen"


class GraphNodeKind(StrEnum):
    PACKAGE = "package"
    FILE = "file"
    CALL_SITE = "call_site"


class GraphEdgeKind(StrEnum):
    DEPENDS_ON = "depends_on"
    IMPORTS = "imports"
    CALLS = "calls"


TERMINAL_INCIDENT_STATUSES: frozenset[IncidentStatus] = frozenset(
    {
        IncidentStatus.COMPLETED,
        IncidentStatus.REJECTED,
        IncidentStatus.CONTESTED,
        IncidentStatus.FAILED,
    }
)

NON_TERMINAL_INCIDENT_STATUSES: frozenset[IncidentStatus] = frozenset(
    {
        IncidentStatus.PENDING,
        IncidentStatus.RUNNING,
        IncidentStatus.AWAITING_REVIEW,
    }
)

TERMINAL_PIPELINE_STAGES: frozenset[PipelineStage] = frozenset(
    {
        PipelineStage.AWAITING_REVIEW,
        PipelineStage.COMPLETED,
        PipelineStage.CONTESTED,
        PipelineStage.FAILED,
    }
)


assert (
    TERMINAL_INCIDENT_STATUSES | NON_TERMINAL_INCIDENT_STATUSES
) == frozenset(IncidentStatus)
assert (
    TERMINAL_INCIDENT_STATUSES & NON_TERMINAL_INCIDENT_STATUSES
) == frozenset()
assert TERMINAL_PIPELINE_STAGES <= frozenset(PipelineStage)
