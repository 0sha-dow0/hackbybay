from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from backend.config import Settings
from backend.domain.constants import (
    REPLACEMENT_PACKAGE,
    TARGET_PACKAGE,
    TRANSPLANT_MAX_ATTEMPTS,
)
from backend.domain.determinism import Clock, IdGenerator
from backend.domain.enums import (
    TERMINAL_PIPELINE_STAGES,
    IncidentStatus,
    PipelineStage,
    SandboxOutcome,
)
from backend.domain.errors import DepCoverError, Err, Ok, Result, SandboxError
from backend.domain.models import (
    BehavioralDiffResult,
    BuildResult,
    ConsensusResult,
    EvidenceBundle,
    FileContent,
    Incident,
    NormalizedOutput,
    PipelineEvent,
    Recipe,
    SanitizedEvidence,
    SurgeryPlan,
    TestResult,
    Transplant,
    TransplantOutput,
    TransplantRequest,
)
from backend.ports.event_sink import EventSink
from backend.ports.record_store import RecordStore
from backend.services.battery import input_battery
from backend.services.incident_state import is_terminal, transition
from backend.services.judges import JudgePanel
from backend.services.recipes import RecipeMemory
from backend.services.transplant_agent import TransplantAgent
from backend.services.transplant_validators import TransplantValidator
from backend.services.verification import VerificationEngine

Sanitizer = Callable[[EvidenceBundle], Result[SanitizedEvidence, DepCoverError]]

_LIBRARY_PAIR_SEPARATOR: Final[str] = "->"
_LIBRARY_PAIR: Final[str] = (
    f"{TARGET_PACKAGE}{_LIBRARY_PAIR_SEPARATOR}{REPLACEMENT_PACKAGE}"
)
_TRANSPLANT_ID_PREFIX: Final[str] = "transplant"

_INITIAL_SEQ: Final[int] = 0

_RECALL_MESSAGE: Final[str] = (
    "recalling the transplant recipe for the target dependency"
)
_VALIDATE_MESSAGE: Final[str] = (
    "validating the rewritten output for syntax and surviving target imports"
)
_VERIFY_BUILD_MESSAGE: Final[str] = (
    "running the sandbox build check against the rewritten files"
)
_VERIFY_TEST_MESSAGE: Final[str] = (
    "running the sandbox test suite against the rewritten files"
)
_VERIFY_BEHAVIORAL_MESSAGE: Final[str] = (
    "running the behavioral differential battery against the rewritten files"
)
_JUDGE_MESSAGE: Final[str] = (
    "deliberating the judge panel over the sanitized evidence bundle"
)
_AWAITING_REVIEW_MESSAGE: Final[str] = (
    "transplant verified and approved; handing off to human review"
)
_CONTESTED_MESSAGE: Final[str] = (
    "transplant contested; consensus was not reached or the evidence "
    "contradicts approval"
)
_REWRITE_MESSAGE_TEMPLATE: Final[str] = (
    "rewriting the affected files to replace the target dependency "
    "(attempt {attempt})"
)

_FAILURE_PREFIX: Final[str] = "pipeline failed: "
_CRASH_PREFIX: Final[str] = "pipeline crashed with an unexpected exception: "

_BUILD_BROKE_MESSAGE: Final[str] = "the sandbox build broke after the transplant"
_ENTRY_CRASH_MESSAGE: Final[str] = (
    "pipeline entry crashed before the incident could enter the running state"
)
_TERMINAL_CRASH_MESSAGE: Final[str] = (
    "pipeline crashed while recording the terminal incident state"
)

_CTX_CAUSE: Final[str] = "cause"
_CTX_OUTCOME: Final[str] = "outcome"


def _rewrite_message(attempt: int) -> str:
    return _REWRITE_MESSAGE_TEMPLATE.format(attempt=attempt)


def _failure_reason(error: DepCoverError) -> str:
    return f"{_FAILURE_PREFIX}{error}"


def _crash_reason(error: Exception) -> str:
    return f"{_CRASH_PREFIX}{error!r}"


@dataclass(frozen=True)
class _Entry:
    incident: Incident
    active: bool


@dataclass(frozen=True)
class _VerifyOutcome:
    build: BuildResult
    test: TestResult
    behavioral: BehavioralDiffResult


class PipelineOrchestrator:
    def __init__(
        self,
        agent: TransplantAgent,
        validator: TransplantValidator,
        verifier: VerificationEngine,
        sanitizer: Sanitizer,
        panel: JudgePanel,
        recipes: RecipeMemory,
        store: RecordStore,
        events: EventSink,
        clock: Clock,
        ids: IdGenerator,
        settings: Settings,
    ) -> None:
        self._agent: TransplantAgent = agent
        self._validator: TransplantValidator = validator
        self._verifier: VerificationEngine = verifier
        self._sanitizer: Sanitizer = sanitizer
        self._panel: JudgePanel = panel
        self._recipes: RecipeMemory = recipes
        self._store: RecordStore = store
        self._events: EventSink = events
        self._clock: Clock = clock
        self._ids: IdGenerator = ids
        self._settings: Settings = settings

    async def run(
        self,
        incident: Incident,
        surgery_plan: SurgeryPlan,
        files: Sequence[FileContent],
        golden: Mapping[str, NormalizedOutput],
    ) -> Result[Incident, DepCoverError]:
        try:
            entry_result = self._enter(incident)
        except Exception as error:
            return Err(
                DepCoverError(_ENTRY_CRASH_MESSAGE, {_CTX_CAUSE: repr(error)})
            )
        if isinstance(entry_result, Err):
            return Err(entry_result.error)
        entry = entry_result.value
        if not entry.active:
            return Ok(entry.incident)
        running = entry.incident
        try:
            return await self._pipeline(running, surgery_plan, files, golden)
        except Exception as error:
            return self._safe_fail(running, _crash_reason(error))

    def _enter(self, incident: Incident) -> Result[_Entry, DepCoverError]:
        reloaded = self._store.get_incident(incident.id)
        if isinstance(reloaded, Err):
            return Err(reloaded.error)
        current = reloaded.value
        if is_terminal(current.status):
            return Ok(_Entry(incident=current, active=False))
        transitioned = transition(
            current, IncidentStatus.RUNNING, self._clock.now()
        )
        if isinstance(transitioned, Err):
            return Err(transitioned.error)
        persisted = self._store.update_incident(
            transitioned.value, current.status
        )
        if isinstance(persisted, Err):
            return Err(persisted.error)
        return Ok(_Entry(incident=persisted.value, active=True))

    async def _pipeline(
        self,
        running: Incident,
        surgery_plan: SurgeryPlan,
        files: Sequence[FileContent],
        golden: Mapping[str, NormalizedOutput],
    ) -> Result[Incident, DepCoverError]:
        recall_published = self._publish(
            running.id, PipelineStage.RECALL, _RECALL_MESSAGE
        )
        if isinstance(recall_published, Err):
            return self._fail(running, _failure_reason(recall_published.error))
        recipe = self._recall_recipe()
        cleaned_result = self._rewrite_and_validate(
            running, surgery_plan, files, recipe
        )
        if isinstance(cleaned_result, Err):
            return self._fail(running, _failure_reason(cleaned_result.error))
        cleaned = cleaned_result.value
        verify_result = self._verify(running, cleaned, golden)
        if isinstance(verify_result, Err):
            return self._fail(running, _failure_reason(verify_result.error))
        verify = verify_result.value
        transplant_id = f"{_TRANSPLANT_ID_PREFIX}-{running.id}"
        bundle = EvidenceBundle(
            transplant_id=transplant_id,
            diff=self._verifier.diff_files(files, cleaned.files),
            build=verify.build,
            test=verify.test,
            behavioral=verify.behavioral,
        )
        judged = await self._judge(running, transplant_id, bundle, recipe)
        if isinstance(judged, Err):
            return self._fail(running, _failure_reason(judged.error))
        consensus = judged.value
        persisted = self._persist(
            transplant_id, running, surgery_plan, bundle, consensus
        )
        if isinstance(persisted, Err):
            return self._fail(running, _failure_reason(persisted.error))
        return self._decide(running, verify, consensus)

    def _recall_recipe(self) -> Recipe | None:
        recalled = self._recipes.recall(_LIBRARY_PAIR)
        if isinstance(recalled, Err):
            return None
        return recalled.value

    def _rewrite_and_validate(
        self,
        running: Incident,
        surgery_plan: SurgeryPlan,
        files: Sequence[FileContent],
        recipe: Recipe | None,
    ) -> Result[TransplantOutput, DepCoverError]:
        attempt = 1
        while True:
            rewrite_published = self._publish(
                running.id, PipelineStage.REWRITE, _rewrite_message(attempt)
            )
            if isinstance(rewrite_published, Err):
                return Err(rewrite_published.error)
            request = TransplantRequest(
                incident_id=running.id,
                surgery_plan=surgery_plan,
                files=tuple(files),
                recipe=recipe,
            )
            output_result = self._agent.run(request, attempt)
            if isinstance(output_result, Err):
                retryable_error: DepCoverError = output_result.error
            else:
                validate_published = self._publish(
                    running.id, PipelineStage.VALIDATE, _VALIDATE_MESSAGE
                )
                if isinstance(validate_published, Err):
                    return Err(validate_published.error)
                validated = self._validator.validate(
                    output_result.value,
                    surgery_plan,
                    self._settings.daytona_snapshot_id,
                )
                if isinstance(validated, Err):
                    retryable_error = validated.error
                else:
                    cleaned, _ = validated.value
                    return Ok(cleaned)
            if attempt >= TRANSPLANT_MAX_ATTEMPTS:
                return Err(retryable_error)
            attempt += 1

    def _verify(
        self,
        running: Incident,
        cleaned: TransplantOutput,
        golden: Mapping[str, NormalizedOutput],
    ) -> Result[_VerifyOutcome, DepCoverError]:
        snapshot_id = self._settings.daytona_snapshot_id
        build_published = self._publish(
            running.id, PipelineStage.VERIFY_BUILD, _VERIFY_BUILD_MESSAGE
        )
        if isinstance(build_published, Err):
            return Err(build_published.error)
        build_result = self._verifier.build_check(cleaned.files, snapshot_id)
        if isinstance(build_result, Err):
            return Err(build_result.error)
        build = build_result.value
        if build.outcome is not SandboxOutcome.PASSED:
            return Err(
                SandboxError(
                    _BUILD_BROKE_MESSAGE, {_CTX_OUTCOME: build.outcome.value}
                )
            )
        test_published = self._publish(
            running.id, PipelineStage.VERIFY_TEST, _VERIFY_TEST_MESSAGE
        )
        if isinstance(test_published, Err):
            return Err(test_published.error)
        test_result = self._verifier.test_suite(cleaned.files, snapshot_id)
        if isinstance(test_result, Err):
            return Err(test_result.error)
        behavioral_published = self._publish(
            running.id,
            PipelineStage.VERIFY_BEHAVIORAL,
            _VERIFY_BEHAVIORAL_MESSAGE,
        )
        if isinstance(behavioral_published, Err):
            return Err(behavioral_published.error)
        behavioral_result = self._verifier.behavioral_diff(
            cleaned.files, golden, input_battery(), snapshot_id
        )
        if isinstance(behavioral_result, Err):
            return Err(behavioral_result.error)
        return Ok(
            _VerifyOutcome(
                build=build,
                test=test_result.value,
                behavioral=behavioral_result.value,
            )
        )

    async def _judge(
        self,
        running: Incident,
        transplant_id: str,
        bundle: EvidenceBundle,
        recipe: Recipe | None,
    ) -> Result[ConsensusResult, DepCoverError]:
        judge_published = self._publish(
            running.id, PipelineStage.JUDGE, _JUDGE_MESSAGE
        )
        if isinstance(judge_published, Err):
            return Err(judge_published.error)
        sanitized = self._sanitizer(bundle)
        if isinstance(sanitized, Err):
            return Err(sanitized.error)
        consensus = await self._panel.deliberate(
            transplant_id, sanitized.value, recipe
        )
        if isinstance(consensus, Err):
            return Err(consensus.error)
        return Ok(consensus.value)

    def _persist(
        self,
        transplant_id: str,
        running: Incident,
        surgery_plan: SurgeryPlan,
        bundle: EvidenceBundle,
        consensus: ConsensusResult,
    ) -> Result[None, DepCoverError]:
        transplant = Transplant(
            id=transplant_id,
            incident_id=running.id,
            surgery_plan=surgery_plan,
            diff=bundle.diff,
            evidence=bundle,
            consensus=consensus,
        )
        saved = self._store.save_transplant(transplant)
        if isinstance(saved, Err):
            return Err(saved.error)
        verdicts_saved = self._store.save_verdicts(consensus.verdicts)
        if isinstance(verdicts_saved, Err):
            return Err(verdicts_saved.error)
        return Ok(None)

    def _decide(
        self,
        running: Incident,
        verify: _VerifyOutcome,
        consensus: ConsensusResult,
    ) -> Result[Incident, DepCoverError]:
        evidence_clean = (
            verify.build.outcome is SandboxOutcome.PASSED
            and verify.test.outcome is SandboxOutcome.PASSED
            and verify.behavioral.matched
        )
        if consensus.approved and evidence_clean:
            return self._terminalize(
                running,
                IncidentStatus.AWAITING_REVIEW,
                PipelineStage.AWAITING_REVIEW,
                _AWAITING_REVIEW_MESSAGE,
            )
        return self._terminalize(
            running,
            IncidentStatus.CONTESTED,
            PipelineStage.CONTESTED,
            _CONTESTED_MESSAGE,
        )

    def _fail(
        self, running: Incident, message: str
    ) -> Result[Incident, DepCoverError]:
        return self._terminalize(
            running, IncidentStatus.FAILED, PipelineStage.FAILED, message
        )

    def _safe_fail(
        self, running: Incident, message: str
    ) -> Result[Incident, DepCoverError]:
        try:
            return self._fail(running, message)
        except Exception as error:
            return Err(
                DepCoverError(
                    _TERMINAL_CRASH_MESSAGE, {_CTX_CAUSE: repr(error)}
                )
            )

    def _terminalize(
        self,
        running: Incident,
        target: IncidentStatus,
        stage: PipelineStage,
        message: str,
    ) -> Result[Incident, DepCoverError]:
        transitioned = transition(running, target, self._clock.now())
        if isinstance(transitioned, Err):
            return Err(transitioned.error)
        persisted = self._store.update_incident(
            transitioned.value, IncidentStatus.RUNNING
        )
        if isinstance(persisted, Err):
            return Err(persisted.error)
        published = self._publish(running.id, stage, message)
        if isinstance(published, Err):
            return Err(published.error)
        return Ok(persisted.value)

    def _publish(
        self, incident_id: str, stage: PipelineStage, message: str
    ) -> Result[None, DepCoverError]:
        event = PipelineEvent(
            incident_id=incident_id,
            stage=stage,
            seq=_INITIAL_SEQ,
            message=message,
            at=self._clock.now(),
            terminal=stage in TERMINAL_PIPELINE_STAGES,
        )
        return self._events.publish(event)


__all__ = ("PipelineOrchestrator",)
