from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from backend.domain.errors import GitHubError, Result
from backend.domain.models import FileContent, PullRequestRef, RewrittenFile


@dataclass(frozen=True)
class PrSummary:
    number: int
    head_sha: str
    changed_files: tuple[str, ...]


@dataclass(frozen=True)
class NewPr:
    title: str
    body: str
    head_branch: str
    base_branch: str
    files: tuple[RewrittenFile, ...]


class GitHubClient(Protocol):
    def open_pr(
        self, repo_url: str, pr: NewPr, idempotency_key: str
    ) -> Result[PullRequestRef, GitHubError]: ...

    def list_open_prs(self, repo_url: str) -> Result[tuple[PrSummary, ...], GitHubError]: ...

    def get_pr_files(
        self, repo_url: str, number: int
    ) -> Result[tuple[FileContent, ...], GitHubError]: ...

    def post_comment(
        self, repo_url: str, number: int, body: str, idempotency_key: str
    ) -> Result[None, GitHubError]: ...


__all__ = ("GitHubClient", "NewPr", "PrSummary")
