from typing import Final

from backend.domain.enums import FileDecisionKind, ReviewDecision
from backend.domain.errors import Err, GitHubError, Ok, Result
from backend.domain.models import (
    PullRequestRef,
    Repo,
    Review,
    RewrittenFile,
    Transplant,
)
from backend.ports.github import GitHubClient, NewPr

_BASE_BRANCH: Final[str] = "main"
_HEAD_BRANCH_PREFIX: Final[str] = "depcover/transplant-"
_PR_TITLE_PREFIX: Final[str] = "DepCover transplant "

_BODY_HEADING: Final[str] = "## DepCover dependency transplant"
_BODY_TRANSPLANT_LABEL: Final[str] = "Transplant: "
_BODY_INCIDENT_LABEL: Final[str] = "Incident: "
_BODY_EVIDENCE_LABEL: Final[str] = "Evidence bundle: transplant "
_BODY_BUILD_LABEL: Final[str] = "Build outcome: "
_BODY_TEST_LABEL: Final[str] = "Test outcome: "
_BODY_BEHAVIORAL_LABEL: Final[str] = "Behavioral diff: "
_BODY_FILES_HEADING: Final[str] = "### Changed files"
_BODY_FILE_BULLET: Final[str] = "- "

_BEHAVIORAL_MATCHED: Final[str] = "matched"
_BEHAVIORAL_MISMATCHED: Final[str] = "mismatched"

_CTX_TRANSPLANT_ID: Final[str] = "transplant_id"
_CTX_REVIEW_TRANSPLANT_ID: Final[str] = "review_transplant_id"
_CTX_DECISION: Final[str] = "decision"
_CTX_REJECTED_PATHS: Final[str] = "rejected_paths"

_REJECTED_PATHS_SEPARATOR: Final[str] = ","


def _behavioral_word(matched: bool) -> str:
    return _BEHAVIORAL_MATCHED if matched else _BEHAVIORAL_MISMATCHED


class PullRequestService:
    def __init__(self, github: GitHubClient) -> None:
        self._github: GitHubClient = github

    def open_for(
        self, repo: Repo, transplant: Transplant, review: Review
    ) -> Result[PullRequestRef, GitHubError]:
        authorization = self._authorized_to_ship(transplant, review)
        if isinstance(authorization, Err):
            return authorization
        files = tuple(
            RewrittenFile(path=file_diff.path, text=file_diff.after)
            for file_diff in transplant.diff
        )
        new_pr = NewPr(
            title=f"{_PR_TITLE_PREFIX}{transplant.id}",
            body=self.build_pr_body(transplant),
            head_branch=f"{_HEAD_BRANCH_PREFIX}{transplant.id}",
            base_branch=_BASE_BRANCH,
            files=files,
        )
        return self._github.open_pr(repo.url, new_pr, transplant.id)

    def build_pr_body(self, transplant: Transplant) -> str:
        evidence = transplant.evidence
        changed_files = "\n".join(
            f"{_BODY_FILE_BULLET}{file_diff.path}" for file_diff in transplant.diff
        )
        return (
            f"{_BODY_HEADING}\n\n"
            f"{_BODY_TRANSPLANT_LABEL}{transplant.id}\n"
            f"{_BODY_INCIDENT_LABEL}{transplant.incident_id}\n"
            f"{_BODY_EVIDENCE_LABEL}{evidence.transplant_id}\n\n"
            f"{_BODY_BUILD_LABEL}{evidence.build.outcome.value}\n"
            f"{_BODY_TEST_LABEL}{evidence.test.outcome.value}\n"
            f"{_BODY_BEHAVIORAL_LABEL}"
            f"{_behavioral_word(evidence.behavioral.matched)}\n\n"
            f"{_BODY_FILES_HEADING}\n"
            f"{changed_files}"
        )

    def _authorized_to_ship(
        self, transplant: Transplant, review: Review
    ) -> Result[None, GitHubError]:
        if review.transplant_id != transplant.id:
            return Err(
                GitHubError(
                    "review does not authorize this transplant",
                    {
                        _CTX_TRANSPLANT_ID: transplant.id,
                        _CTX_REVIEW_TRANSPLANT_ID: review.transplant_id,
                    },
                )
            )
        if review.decision is not ReviewDecision.ACCEPT_ALL:
            return Err(
                GitHubError(
                    "review decision is not a full accept",
                    {
                        _CTX_TRANSPLANT_ID: transplant.id,
                        _CTX_DECISION: review.decision.value,
                    },
                )
            )
        if len(review.per_file) == 0:
            return Err(
                GitHubError(
                    "review records no per-file accept decisions",
                    {_CTX_TRANSPLANT_ID: transplant.id},
                )
            )
        rejected_paths = tuple(
            file_decision.path
            for file_decision in review.per_file
            if file_decision.kind is not FileDecisionKind.ACCEPT
        )
        if len(rejected_paths) > 0:
            return Err(
                GitHubError(
                    "review rejects one or more files",
                    {
                        _CTX_TRANSPLANT_ID: transplant.id,
                        _CTX_REJECTED_PATHS: _REJECTED_PATHS_SEPARATOR.join(
                            rejected_paths
                        ),
                    },
                )
            )
        if len(transplant.diff) == 0:
            return Err(
                GitHubError(
                    "transplant has no files to ship",
                    {_CTX_TRANSPLANT_ID: transplant.id},
                )
            )
        return Ok(None)


__all__ = ("PullRequestService",)
