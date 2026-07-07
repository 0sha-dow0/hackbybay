"""Tests for backend.services.pull_request.PullRequestService (Unit 31).

Verifies the AMENDED contract of the store-free PullRequestService:

* the constructor takes only a GitHubClient (no store);
* ``open_for`` opens a PR ONLY when the review is a full accept
  (decision ACCEPT_ALL, every per-file kind ACCEPT), the review authorizes
  this exact transplant (review.transplant_id == transplant.id), and the
  transplant has a non-empty diff; any violation is Err(GitHubError) and
  opens no PR;
* a successful open ships exactly one RewrittenFile per FileDiff
  (path=diff.path, text=diff.after) and is keyed on transplant.id, so a
  repeat call returns the same PullRequestRef and leaves exactly one PR;
* rate limiting surfaces as Err(RateLimitError);
* ``build_pr_body`` is deterministic, non-empty, and references the evidence
  bundle (build outcome, test outcome, behavioral matched/mismatched, and the
  changed file paths).
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import TypeVar

import pytest

from backend.adapters.fake.fake_github import FakeGitHubClient, SeededPr
from backend.domain.enums import (
    FileDecisionKind,
    JudgeName,
    ReviewDecision,
    SandboxOutcome,
    Verdict,
)
from backend.domain.errors import (
    DepCoverError,
    Err,
    GitHubError,
    Ok,
    RateLimitError,
    Result,
)
from backend.domain.models import (
    BehavioralDiffResult,
    BuildResult,
    ConsensusResult,
    EvidenceBundle,
    FileDecision,
    FileDiff,
    JudgeVerdict,
    PullRequestRef,
    Repo,
    Review,
    SurgeryPlan,
    Transplant,
)
from backend.domain.models import TestResult as SandboxTestResult
from backend.ports.github import PrSummary
from backend.services.pull_request import PullRequestService

_T = TypeVar("_T")
_E = TypeVar("_E", bound=DepCoverError)

_REPO_URL: str = "https://github.com/acme/widget"
_TRANSPLANT_ID: str = "tr-001"
_INCIDENT_ID: str = "inc-001"
_USER_ID: str = "user-42"

_PATH_A: str = "src/api/client.py"
_PATH_B: str = "src/api/handler.py"


# --------------------------------------------------------------------------- #
# Result narrowing helpers (isinstance-narrowed for mypy strict).
# --------------------------------------------------------------------------- #
def unwrap_ok(result: Result[_T, _E]) -> _T:
    assert isinstance(result, Ok), f"expected Ok, got {result!r}"
    return result.value


def unwrap_err(result: Result[_T, _E]) -> _E:
    assert isinstance(result, Err), f"expected Err, got {result!r}"
    return result.error


# --------------------------------------------------------------------------- #
# Fixture builders. Everything frozen/valid per the domain validators.
# --------------------------------------------------------------------------- #
def make_repo(url: str = _REPO_URL) -> Repo:
    return Repo(
        id="repo-1",
        url=url,
        owner="acme",
        registered_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def make_diff(path: str, after: str) -> FileDiff:
    return FileDiff(
        path=path,
        unified_diff=f"--- a/{path}\n+++ b/{path}\n@@\n-old\n+{after}",
        before="old",
        after=after,
    )


def make_evidence(
    transplant_id: str,
    diff: tuple[FileDiff, ...],
    *,
    build_outcome: SandboxOutcome = SandboxOutcome.PASSED,
    test_outcome: SandboxOutcome = SandboxOutcome.PASSED,
    matched: bool = True,
) -> EvidenceBundle:
    return EvidenceBundle(
        transplant_id=transplant_id,
        diff=diff,
        build=BuildResult(outcome=build_outcome, log="build log"),
        test=SandboxTestResult(outcome=test_outcome, failing_tests=(), log="test log"),
        behavioral=BehavioralDiffResult(matched=matched, per_case=()),
    )


def make_consensus() -> ConsensusResult:
    # panel_size=4, approvals=4 >= required 3 -> approved True, contested False.
    return ConsensusResult(
        approvals=4,
        panel_size=4,
        approved=True,
        contested=False,
        verdicts=(
            JudgeVerdict(
                transplant_id=_TRANSPLANT_ID,
                judge_name=JudgeName.CORRECTNESS,
                verdict=Verdict.APPROVE,
                rationale="ok",
            ),
        ),
    )


def make_transplant(
    transplant_id: str = _TRANSPLANT_ID,
    diff: tuple[FileDiff, ...] | None = None,
    *,
    build_outcome: SandboxOutcome = SandboxOutcome.PASSED,
    test_outcome: SandboxOutcome = SandboxOutcome.PASSED,
    matched: bool = True,
) -> Transplant:
    if diff is None:
        diff = (make_diff(_PATH_A, "content-a"), make_diff(_PATH_B, "content-b"))
    return Transplant(
        id=transplant_id,
        incident_id=_INCIDENT_ID,
        # Empty surgery plan is the simplest valid one (affected_files == ()).
        surgery_plan=SurgeryPlan(
            target_package="axios", call_sites=(), affected_files=()
        ),
        diff=diff,
        evidence=make_evidence(
            transplant_id,
            diff,
            build_outcome=build_outcome,
            test_outcome=test_outcome,
            matched=matched,
        ),
        consensus=make_consensus(),
    )


def make_review(
    *,
    transplant_id: str = _TRANSPLANT_ID,
    decision: ReviewDecision = ReviewDecision.ACCEPT_ALL,
    per_file: tuple[FileDecision, ...] | None = None,
) -> Review:
    if per_file is None:
        per_file = (
            FileDecision(path=_PATH_A, kind=FileDecisionKind.ACCEPT, reason=None),
            FileDecision(path=_PATH_B, kind=FileDecisionKind.ACCEPT, reason=None),
        )
    return Review(
        transplant_id=transplant_id,
        user_id=_USER_ID,
        decision=decision,
        per_file=per_file,
        reason=None,
    )


def make_client(*, rate_limited: bool = False) -> FakeGitHubClient:
    seeded: dict[str, tuple[SeededPr, ...]] = {_REPO_URL: ()}
    return FakeGitHubClient(seeded, rate_limited=rate_limited)


# --------------------------------------------------------------------------- #
# Case 1: constructor takes only github.
# --------------------------------------------------------------------------- #
def test_constructor_takes_only_github() -> None:
    service = PullRequestService(make_client())
    assert isinstance(service, PullRequestService)


# --------------------------------------------------------------------------- #
# Case 2: full accept -> Ok(PullRequestRef); files are one-per-diff.
# --------------------------------------------------------------------------- #
def test_full_accept_opens_pr_and_returns_ref() -> None:
    client = make_client()
    service = PullRequestService(client)
    repo = make_repo()
    transplant = make_transplant()
    review = make_review()

    result = service.open_for(repo, transplant, review)
    ref = unwrap_ok(result)
    assert isinstance(ref, PullRequestRef)
    assert ref.number == 1
    assert ref.url == f"{_REPO_URL}/pull/1"


def test_full_accept_ships_one_rewritten_file_per_diff() -> None:
    client = make_client()
    service = PullRequestService(client)
    repo = make_repo()
    diff = (make_diff(_PATH_A, "after-a"), make_diff(_PATH_B, "after-b"))
    transplant = make_transplant(diff=diff)
    review = make_review()

    ref = unwrap_ok(service.open_for(repo, transplant, review))

    files = unwrap_ok(client.get_pr_files(repo.url, ref.number))
    # Exactly one file per FileDiff, each carrying (path, after).
    assert len(files) == len(diff)
    observed = {(f.path, f.text) for f in files}
    expected = {(d.path, d.after) for d in diff}
    assert observed == expected


# --------------------------------------------------------------------------- #
# Case 3: any per-file REJECT -> Err, no PR opened.
# --------------------------------------------------------------------------- #
def test_any_per_file_reject_is_err_and_opens_no_pr() -> None:
    client = make_client()
    service = PullRequestService(client)
    repo = make_repo()
    transplant = make_transplant()
    review = make_review(
        per_file=(
            FileDecision(path=_PATH_A, kind=FileDecisionKind.ACCEPT, reason=None),
            FileDecision(
                path=_PATH_B, kind=FileDecisionKind.REJECT, reason="unsafe"
            ),
        )
    )

    error = unwrap_err(service.open_for(repo, transplant, review))
    assert isinstance(error, GitHubError)
    assert not isinstance(error, RateLimitError)
    # No PR was opened.
    assert unwrap_ok(client.list_open_prs(repo.url)) == ()


def test_single_per_file_reject_only_is_err() -> None:
    client = make_client()
    service = PullRequestService(client)
    repo = make_repo()
    transplant = make_transplant(diff=(make_diff(_PATH_A, "after-a"),))
    review = make_review(
        per_file=(
            FileDecision(path=_PATH_A, kind=FileDecisionKind.REJECT, reason="no"),
        )
    )

    error = unwrap_err(service.open_for(repo, transplant, review))
    assert isinstance(error, GitHubError)
    assert unwrap_ok(client.list_open_prs(repo.url)) == ()


# --------------------------------------------------------------------------- #
# Case 4: decision != ACCEPT_ALL -> Err.
# --------------------------------------------------------------------------- #
def test_decision_reject_is_err_even_with_all_files_accept() -> None:
    client = make_client()
    service = PullRequestService(client)
    repo = make_repo()
    transplant = make_transplant()
    # All per-file ACCEPT but the top-level decision is REJECT.
    review = make_review(decision=ReviewDecision.REJECT)

    error = unwrap_err(service.open_for(repo, transplant, review))
    assert isinstance(error, GitHubError)
    assert unwrap_ok(client.list_open_prs(repo.url)) == ()


# --------------------------------------------------------------------------- #
# Case 5: review.transplant_id != transplant.id -> Err.
# --------------------------------------------------------------------------- #
def test_mismatched_transplant_id_is_err() -> None:
    client = make_client()
    service = PullRequestService(client)
    repo = make_repo()
    transplant = make_transplant(transplant_id="tr-001")
    # Everything else is a valid full-accept, but authorizes a different id.
    review = make_review(transplant_id="tr-999")

    error = unwrap_err(service.open_for(repo, transplant, review))
    assert isinstance(error, GitHubError)
    assert unwrap_ok(client.list_open_prs(repo.url)) == ()


# --------------------------------------------------------------------------- #
# Case 6: empty per_file -> Err; empty diff -> Err.
# --------------------------------------------------------------------------- #
def test_empty_per_file_is_err() -> None:
    client = make_client()
    service = PullRequestService(client)
    repo = make_repo()
    transplant = make_transplant()
    review = make_review(per_file=())

    error = unwrap_err(service.open_for(repo, transplant, review))
    assert isinstance(error, GitHubError)
    assert unwrap_ok(client.list_open_prs(repo.url)) == ()


def test_empty_transplant_diff_is_err() -> None:
    client = make_client()
    service = PullRequestService(client)
    repo = make_repo()
    transplant = make_transplant(diff=())
    # A non-empty accepting review, but nothing to ship.
    review = make_review(
        per_file=(
            FileDecision(path=_PATH_A, kind=FileDecisionKind.ACCEPT, reason=None),
        )
    )

    error = unwrap_err(service.open_for(repo, transplant, review))
    assert isinstance(error, GitHubError)
    assert unwrap_ok(client.list_open_prs(repo.url)) == ()


# --------------------------------------------------------------------------- #
# Case 7: idempotency keyed on transplant.id.
# --------------------------------------------------------------------------- #
def test_open_for_is_idempotent_on_transplant_id() -> None:
    client = make_client()
    service = PullRequestService(client)
    repo = make_repo()
    transplant = make_transplant()
    review = make_review()

    first = unwrap_ok(service.open_for(repo, transplant, review))
    second = unwrap_ok(service.open_for(repo, transplant, review))

    assert first == second
    # Exactly one PR exists.
    summaries = unwrap_ok(client.list_open_prs(repo.url))
    assert len(summaries) == 1
    assert summaries[0].number == first.number


def test_idempotency_survives_concurrent_open_for() -> None:
    client = make_client()
    service = PullRequestService(client)
    repo = make_repo()
    transplant = make_transplant()
    review = make_review()

    barrier = threading.Barrier(8)
    results: list[Result[PullRequestRef, GitHubError]] = []
    results_lock = threading.Lock()

    def worker() -> None:
        barrier.wait()
        result = service.open_for(repo, transplant, review)
        with results_lock:
            results.append(result)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    refs = [unwrap_ok(result) for result in results]
    assert len(refs) == 8
    assert all(ref == refs[0] for ref in refs)
    # Idempotency key = transplant.id -> exactly one PR despite the race.
    summaries = unwrap_ok(client.list_open_prs(repo.url))
    assert len(summaries) == 1


def test_distinct_transplants_open_distinct_prs() -> None:
    client = make_client()
    service = PullRequestService(client)
    repo = make_repo()

    transplant_a = make_transplant(transplant_id="tr-A")
    transplant_b = make_transplant(transplant_id="tr-B")
    review_a = make_review(transplant_id="tr-A")
    review_b = make_review(transplant_id="tr-B")

    ref_a = unwrap_ok(service.open_for(repo, transplant_a, review_a))
    ref_b = unwrap_ok(service.open_for(repo, transplant_b, review_b))

    assert ref_a.number != ref_b.number
    summaries: tuple[PrSummary, ...] = unwrap_ok(client.list_open_prs(repo.url))
    assert len(summaries) == 2


# --------------------------------------------------------------------------- #
# Case 8: rate limiting.
# --------------------------------------------------------------------------- #
def test_rate_limited_client_yields_rate_limit_error() -> None:
    client = make_client(rate_limited=True)
    service = PullRequestService(client)
    repo = make_repo()
    transplant = make_transplant()
    review = make_review()

    error = unwrap_err(service.open_for(repo, transplant, review))
    assert isinstance(error, RateLimitError)


# --------------------------------------------------------------------------- #
# Case 9: build_pr_body determinism and evidence references.
# --------------------------------------------------------------------------- #
def test_build_pr_body_is_non_empty_and_deterministic() -> None:
    service = PullRequestService(make_client())
    transplant = make_transplant()

    body_first = service.build_pr_body(transplant)
    body_second = service.build_pr_body(transplant)

    assert body_first != ""
    assert body_first == body_second


def test_build_pr_body_identical_for_equal_transplants() -> None:
    service = PullRequestService(make_client())
    # Two structurally-identical but distinct instances.
    transplant_one = make_transplant()
    transplant_two = make_transplant()
    assert transplant_one is not transplant_two

    assert service.build_pr_body(transplant_one) == service.build_pr_body(
        transplant_two
    )


def test_build_pr_body_references_build_and_test_outcomes() -> None:
    service = PullRequestService(make_client())
    transplant = make_transplant(
        build_outcome=SandboxOutcome.PASSED,
        test_outcome=SandboxOutcome.TIMEOUT,
    )

    body = service.build_pr_body(transplant)

    assert SandboxOutcome.PASSED.value in body  # "passed" -> build outcome
    assert SandboxOutcome.TIMEOUT.value in body  # "timeout" -> test outcome


def test_build_pr_body_references_changed_file_paths() -> None:
    service = PullRequestService(make_client())
    diff = (make_diff(_PATH_A, "after-a"), make_diff(_PATH_B, "after-b"))
    transplant = make_transplant(diff=diff)

    body = service.build_pr_body(transplant)

    for file_diff in diff:
        assert file_diff.path in body


def test_build_pr_body_behavioral_matched_indicator() -> None:
    service = PullRequestService(make_client())
    transplant = make_transplant(matched=True)

    body = service.build_pr_body(transplant)

    # "matched" present as the behavioral indicator; "mismatched" must be absent.
    assert "mismatched" not in body
    assert "matched" in body


def test_build_pr_body_behavioral_mismatched_indicator() -> None:
    service = PullRequestService(make_client())
    transplant = make_transplant(matched=False)

    body = service.build_pr_body(transplant)

    assert "mismatched" in body


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
