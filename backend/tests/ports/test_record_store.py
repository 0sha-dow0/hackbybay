"""Tests for Unit 9: RecordStore port + FakeRecordStore.

Every fallible operation returns a typed ``Result``; these tests narrow with
``isinstance`` against ``Ok``/``Err`` (never truthiness) and bind assertions to
the published contract: idempotent-by-key writes, no-silent-overwrite on
differing content, optimistic-concurrency + terminal guards on
``update_incident``, atomic ``save_verdicts``, recipe upsert/absence semantics,
and determinism under an identical ``FixedClock``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.adapters.fake.fake_record_store import FakeRecordStore
from backend.domain.determinism import FixedClock
from backend.domain.enums import (
    IncidentStatus,
    JudgeName,
    SandboxOutcome,
    StrategyKind,
    TriggerType,
    Verdict,
)
from backend.domain.errors import Err, Ok, RecordStoreError
from backend.domain.models import (
    BehavioralDiffResult,
    BuildResult,
    ConsensusResult,
    EvidenceBundle,
    Incident,
    JudgeVerdict,
    Recipe,
    Repo,
    SurgeryPlan,
    Transplant,
    UnderwritingReport,
    GraphLayout,
)
from backend.domain.models import TestResult as SandboxTestResult
from backend.ports.record_store import RecordStore

CLOCK_START: datetime = datetime(2026, 7, 7, 12, 0, 0, tzinfo=timezone.utc)
CLOCK_STEP_S: float = 5.0
CREATED_AT: datetime = datetime(2020, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def _store() -> FakeRecordStore:
    return FakeRecordStore(FixedClock(CLOCK_START, CLOCK_STEP_S))


def _repo(repo_id: str = "repo-1", url: str = "https://example/r") -> Repo:
    return Repo(id=repo_id, url=url, owner="owner", registered_at=CREATED_AT)


def _underwriting(repo_id: str = "repo-1", affected: int = 3) -> UnderwritingReport:
    return UnderwritingReport(
        id="uw-1",
        repo_id=repo_id,
        target_package="axios",
        failing_tests=("t.spec.js",),
        affected_file_count=affected,
        centrality=(),
        graph_layout=GraphLayout(nodes=(), edges=()),
        warnings=(),
        created_at=CREATED_AT,
    )


def _incident(
    incident_id: str = "inc-1",
    status: IncidentStatus = IncidentStatus.PENDING,
) -> Incident:
    return Incident(
        id=incident_id,
        repo_id="repo-1",
        trigger_type=TriggerType.MOCK_CVE,
        chosen_strategy=None,
        status=status,
        created_at=CREATED_AT,
        updated_at=CREATED_AT,
    )


def _transplant(transplant_id: str = "tp-1", incident_id: str = "inc-1") -> Transplant:
    plan = SurgeryPlan(target_package="axios", call_sites=(), affected_files=())
    evidence = EvidenceBundle(
        transplant_id=transplant_id,
        diff=(),
        build=BuildResult(outcome=SandboxOutcome.PASSED, log=""),
        test=SandboxTestResult(outcome=SandboxOutcome.PASSED, failing_tests=(), log=""),
        behavioral=BehavioralDiffResult(matched=True, per_case=()),
    )
    consensus = ConsensusResult(
        approvals=3, panel_size=4, approved=True, contested=False, verdicts=()
    )
    return Transplant(
        id=transplant_id,
        incident_id=incident_id,
        surgery_plan=plan,
        diff=(),
        evidence=evidence,
        consensus=consensus,
    )


def _verdict(
    transplant_id: str = "tp-1",
    judge: JudgeName = JudgeName.CORRECTNESS,
    verdict: Verdict = Verdict.APPROVE,
    rationale: str = "ok",
) -> JudgeVerdict:
    return JudgeVerdict(
        transplant_id=transplant_id,
        judge_name=judge,
        verdict=verdict,
        rationale=rationale,
    )


def _recipe(pair: str = "axios->fetch", fix: str = "fetch wrapper") -> Recipe:
    return Recipe(
        id="rec-1",
        library_pair=pair,
        wrapper_pattern="throw on non-2xx",
        known_gaps=(),
        confirmed_fix=fix,
    )


# --- Case 1: get-before-create for every read-strict entity ----------------


def test_get_repo_before_create_is_err() -> None:
    result = _store().get_repo("missing")
    assert isinstance(result, Err)
    assert isinstance(result.error, RecordStoreError)


def test_get_incident_before_create_is_err() -> None:
    result = _store().get_incident("missing")
    assert isinstance(result, Err)
    assert isinstance(result.error, RecordStoreError)


def test_get_transplant_before_create_is_err() -> None:
    result = _store().get_transplant("missing")
    assert isinstance(result, Err)
    assert isinstance(result.error, RecordStoreError)


def test_get_underwriting_before_create_is_err() -> None:
    result = _store().get_underwriting("missing-repo")
    assert isinstance(result, Err)
    assert isinstance(result.error, RecordStoreError)


# --- Case 2: create_repo idempotency + no silent overwrite ------------------


def test_create_repo_then_get_returns_same() -> None:
    store = _store()
    repo = _repo()
    created = store.create_repo(repo)
    assert isinstance(created, Ok)
    assert created.value == repo
    fetched = store.get_repo(repo.id)
    assert isinstance(fetched, Ok)
    assert fetched.value == repo


def test_recreate_identical_repo_is_ok_existing() -> None:
    store = _store()
    repo = _repo()
    assert isinstance(store.create_repo(repo), Ok)
    again = store.create_repo(repo)
    assert isinstance(again, Ok)
    assert again.value == repo


def test_create_repo_same_id_different_content_is_err() -> None:
    store = _store()
    assert isinstance(store.create_repo(_repo(url="https://a")), Ok)
    conflict = store.create_repo(_repo(url="https://DIFFERENT"))
    assert isinstance(conflict, Err)
    assert isinstance(conflict.error, RecordStoreError)
    persisted = store.get_repo("repo-1")
    assert isinstance(persisted, Ok)
    assert persisted.value.url == "https://a"


# --- Case 3: update_incident happy path stamps updated_at from the clock ----


def test_update_incident_happy_stamps_and_advances_clock() -> None:
    store = _store()
    assert isinstance(store.create_incident(_incident()), Ok)

    running = _incident(status=IncidentStatus.RUNNING)
    first = store.update_incident(running, expected_status=IncidentStatus.PENDING)
    assert isinstance(first, Ok)
    assert first.value.status is IncidentStatus.RUNNING
    assert first.value.updated_at == CLOCK_START

    awaiting = first.value.model_copy(
        update={"status": IncidentStatus.AWAITING_REVIEW}
    )
    second = store.update_incident(awaiting, expected_status=IncidentStatus.RUNNING)
    assert isinstance(second, Ok)
    assert second.value.updated_at == CLOCK_START + timedelta(seconds=CLOCK_STEP_S)
    assert second.value.updated_at > first.value.updated_at

    stored = store.get_incident("inc-1")
    assert isinstance(stored, Ok)
    assert stored.value.status is IncidentStatus.AWAITING_REVIEW


# --- Case 4: optimistic concurrency (stale expected_status) -----------------


def test_update_incident_stale_expected_status_is_err() -> None:
    store = _store()
    assert isinstance(store.create_incident(_incident()), Ok)
    stale = store.update_incident(
        _incident(status=IncidentStatus.RUNNING),
        expected_status=IncidentStatus.RUNNING,
    )
    assert isinstance(stale, Err)
    assert isinstance(stale.error, RecordStoreError)
    unchanged = store.get_incident("inc-1")
    assert isinstance(unchanged, Ok)
    assert unchanged.value.status is IncidentStatus.PENDING


def test_update_incident_unknown_id_is_err() -> None:
    store = _store()
    result = store.update_incident(
        _incident(), expected_status=IncidentStatus.PENDING
    )
    assert isinstance(result, Err)
    assert isinstance(result.error, RecordStoreError)


# --- Case 5: never overwrite a terminal incident ---------------------------


def test_update_incident_on_terminal_stored_is_err() -> None:
    store = _store()
    terminal = _incident(status=IncidentStatus.COMPLETED)
    assert isinstance(store.create_incident(terminal), Ok)
    attempt = store.update_incident(
        _incident(status=IncidentStatus.RUNNING),
        expected_status=IncidentStatus.COMPLETED,
    )
    assert isinstance(attempt, Err)
    assert "terminal" in attempt.error.message
    stored = store.get_incident("inc-1")
    assert isinstance(stored, Ok)
    assert stored.value.status is IncidentStatus.COMPLETED


# --- Case 6: save_transplant idempotency + no silent overwrite --------------


def test_save_transplant_then_get_returns_same() -> None:
    store = _store()
    transplant = _transplant()
    saved = store.save_transplant(transplant)
    assert isinstance(saved, Ok)
    assert saved.value == transplant
    fetched = store.get_transplant(transplant.id)
    assert isinstance(fetched, Ok)
    assert fetched.value == transplant


def test_resave_identical_transplant_is_ok_existing() -> None:
    store = _store()
    transplant = _transplant()
    assert isinstance(store.save_transplant(transplant), Ok)
    again = store.save_transplant(transplant)
    assert isinstance(again, Ok)
    assert again.value == transplant


def test_save_transplant_same_id_different_content_is_err() -> None:
    store = _store()
    assert isinstance(store.save_transplant(_transplant(incident_id="inc-1")), Ok)
    conflict = store.save_transplant(_transplant(incident_id="inc-OTHER"))
    assert isinstance(conflict, Err)
    assert isinstance(conflict.error, RecordStoreError)
    persisted = store.get_transplant("tp-1")
    assert isinstance(persisted, Ok)
    assert persisted.value.incident_id == "inc-1"


# --- Case 7: recipe absence + upsert-replace-in-place -----------------------


def test_find_recipe_absent_is_ok_none() -> None:
    result = _store().find_recipe("nonexistent->pair")
    assert isinstance(result, Ok)
    assert result.value is None


def test_upsert_then_find_recipe_returns_recipe() -> None:
    store = _store()
    recipe = _recipe()
    upserted = store.upsert_recipe(recipe)
    assert isinstance(upserted, Ok)
    assert upserted.value == recipe
    found = store.find_recipe(recipe.library_pair)
    assert isinstance(found, Ok)
    assert found.value == recipe


def test_reupsert_same_pair_replaces_in_place() -> None:
    store = _store()
    assert isinstance(store.upsert_recipe(_recipe(fix="old")), Ok)
    replacement = _recipe(fix="new")
    assert isinstance(store.upsert_recipe(replacement), Ok)
    found = store.find_recipe("axios->fetch")
    assert isinstance(found, Ok)
    assert found.value == replacement


# --- Case 8: save_verdicts atomicity + empty sequence -----------------------


def test_save_verdicts_empty_sequence_is_ok_none() -> None:
    result = _store().save_verdicts([])
    assert isinstance(result, Ok)
    assert result.value is None


def test_save_verdicts_happy_and_idempotent() -> None:
    store = _store()
    batch = [
        _verdict(judge=JudgeName.CORRECTNESS),
        _verdict(judge=JudgeName.SECURITY),
    ]
    assert isinstance(store.save_verdicts(batch), Ok)
    assert isinstance(store.save_verdicts(batch), Ok)


def test_save_verdicts_internal_conflict_aborts_before_any_write() -> None:
    store = _store()
    batch = [
        _verdict(judge=JudgeName.CORRECTNESS, verdict=Verdict.APPROVE),
        _verdict(judge=JudgeName.SECURITY, verdict=Verdict.APPROVE),
        _verdict(judge=JudgeName.SECURITY, verdict=Verdict.REJECT),
    ]
    conflict = store.save_verdicts(batch)
    assert isinstance(conflict, Err)
    assert isinstance(conflict.error, RecordStoreError)
    rewrite_correctness = store.save_verdicts(
        [_verdict(judge=JudgeName.CORRECTNESS, verdict=Verdict.REJECT)]
    )
    assert isinstance(rewrite_correctness, Ok)


def test_save_verdicts_conflict_with_stored_aborts_before_any_write() -> None:
    store = _store()
    assert isinstance(
        store.save_verdicts([_verdict(judge=JudgeName.CORRECTNESS)]), Ok
    )
    batch = [
        _verdict(judge=JudgeName.MINIMALITY, verdict=Verdict.APPROVE),
        _verdict(judge=JudgeName.CORRECTNESS, verdict=Verdict.REJECT),
    ]
    conflict = store.save_verdicts(batch)
    assert isinstance(conflict, Err)
    assert isinstance(conflict.error, RecordStoreError)
    rewrite_minimality = store.save_verdicts(
        [_verdict(judge=JudgeName.MINIMALITY, verdict=Verdict.REJECT)]
    )
    assert isinstance(rewrite_minimality, Ok)


# --- Underwriting write semantics (idempotency + no silent overwrite) -------


def test_save_underwriting_idempotent_and_no_silent_overwrite() -> None:
    store = _store()
    report = _underwriting()
    assert isinstance(store.save_underwriting(report), Ok)
    assert isinstance(store.save_underwriting(report), Ok)
    conflict = store.save_underwriting(_underwriting(affected=99))
    assert isinstance(conflict, Err)
    assert isinstance(conflict.error, RecordStoreError)
    persisted = store.get_underwriting("repo-1")
    assert isinstance(persisted, Ok)
    assert persisted.value.affected_file_count == 3


# --- Case 9: determinism under an identical FixedClock ----------------------


def _run_sequence(store: RecordStore) -> Incident:
    store.create_incident(_incident())
    store.update_incident(
        _incident(status=IncidentStatus.RUNNING),
        expected_status=IncidentStatus.PENDING,
    )
    final = store.update_incident(
        _incident(status=IncidentStatus.AWAITING_REVIEW),
        expected_status=IncidentStatus.RUNNING,
    )
    assert isinstance(final, Ok)
    return final.value


def test_identical_clock_yields_identical_state_and_stamps() -> None:
    left = _run_sequence(_store())
    right = _run_sequence(_store())
    assert left == right
    assert left.updated_at == right.updated_at


# --- Case 10: structural conformance to the RecordStore Protocol ------------


def test_fake_conforms_to_record_store_protocol() -> None:
    store: RecordStore = FakeRecordStore(FixedClock(CLOCK_START, CLOCK_STEP_S))
    result = store.find_recipe("axios->fetch")
    assert isinstance(result, Ok)
    assert result.value is None
