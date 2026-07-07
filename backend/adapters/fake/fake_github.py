from __future__ import annotations

import hashlib
import posixpath
import threading
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from backend.domain.errors import Err, GitHubError, Ok, RateLimitError, Result
from backend.domain.models import FileContent, PullRequestRef, RewrittenFile
from backend.ports.github import GitHubClient, NewPr, PrSummary

_FIRST_PR_NUMBER = 1
_HASH_FIELD_SEPARATOR = b"\x00"


@dataclass(frozen=True)
class SeededPr:
    number: int
    head_sha: str
    files: tuple[FileContent, ...]


@dataclass(frozen=True)
class RecordedComment:
    pr_number: int
    body: str
    idempotency_key: str


@dataclass(frozen=True)
class _PrRecord:
    number: int
    head_sha: str
    files: tuple[FileContent, ...]


@dataclass
class _RepoState:
    prs: dict[int, _PrRecord]
    open_pr_keys: dict[str, int]
    comment_keys: set[tuple[int, str]]
    comments: list[RecordedComment]
    next_number: int


def _normalize_repo_path(path: str) -> str | None:
    if not path:
        return None
    normalized = posixpath.normpath(path.replace("\\", "/"))
    if normalized in {".", ".."}:
        return None
    if normalized.startswith("/") or normalized.startswith("../"):
        return None
    return normalized


def _normalize_files(paths_texts: Iterable[tuple[str, str]]) -> tuple[FileContent, ...] | None:
    seen: set[str] = set()
    normalized: list[FileContent] = []
    for path, text in paths_texts:
        canonical = _normalize_repo_path(path)
        if canonical is None or canonical in seen:
            return None
        seen.add(canonical)
        normalized.append(FileContent(path=canonical, text=text))
    return tuple(sorted(normalized, key=lambda file: file.path))


def _compute_head_sha(pr: NewPr) -> str:
    hasher = hashlib.sha256()
    for field in (pr.title, pr.body, pr.head_branch, pr.base_branch):
        hasher.update(field.encode("utf-8"))
        hasher.update(_HASH_FIELD_SEPARATOR)
    for file in pr.files:
        hasher.update(file.path.encode("utf-8"))
        hasher.update(_HASH_FIELD_SEPARATOR)
        hasher.update(file.text.encode("utf-8"))
        hasher.update(_HASH_FIELD_SEPARATOR)
    return hasher.hexdigest()


def _build_repo_state(repo_url: str, seeded_prs: Sequence[SeededPr]) -> _RepoState:
    if not repo_url:
        raise ValueError("FakeGitHubClient repo_url must be non-empty")
    prs: dict[int, _PrRecord] = {}
    for seeded in seeded_prs:
        if seeded.number < _FIRST_PR_NUMBER:
            raise ValueError(f"seeded PR number {seeded.number!r} must be positive")
        if seeded.number in prs:
            raise ValueError(f"duplicate seeded PR number {seeded.number!r} in {repo_url!r}")
        if not seeded.head_sha:
            raise ValueError(f"seeded PR {seeded.number!r} must have a non-empty head_sha")
        files = _normalize_files((file.path, file.text) for file in seeded.files)
        if files is None:
            raise ValueError(
                f"seeded PR {seeded.number!r} in {repo_url!r} has invalid or duplicate paths"
            )
        prs[seeded.number] = _PrRecord(
            number=seeded.number, head_sha=seeded.head_sha, files=files
        )
    next_number = max(prs, default=_FIRST_PR_NUMBER - 1) + 1
    return _RepoState(
        prs=prs,
        open_pr_keys={},
        comment_keys=set(),
        comments=[],
        next_number=next_number,
    )


class FakeGitHubClient(GitHubClient):
    def __init__(
        self,
        repos: Mapping[str, Sequence[SeededPr]],
        *,
        rate_limited: bool = False,
    ) -> None:
        self._repos: dict[str, _RepoState] = {
            repo_url: _build_repo_state(repo_url, seeded_prs)
            for repo_url, seeded_prs in repos.items()
        }
        self._rate_limited = rate_limited
        self._lock = threading.Lock()

    def open_pr(
        self, repo_url: str, pr: NewPr, idempotency_key: str
    ) -> Result[PullRequestRef, GitHubError]:
        with self._lock:
            if self._rate_limited:
                return self._rate_limit_error()
            repo = self._repos.get(repo_url)
            if repo is None:
                return self._unknown_repo_error(repo_url)
            if not idempotency_key:
                return self._github_error("open_pr requires a non-empty idempotency_key")
            existing_number = repo.open_pr_keys.get(idempotency_key)
            if existing_number is not None:
                return Ok(self._pr_ref(repo_url, existing_number))
            files = _normalize_files((file.path, file.text) for file in pr.files)
            if files is None:
                return self._github_error("NewPr contains invalid or duplicate file paths")
            number = repo.next_number
            repo.next_number += 1
            repo.prs[number] = _PrRecord(
                number=number, head_sha=_compute_head_sha(pr), files=files
            )
            repo.open_pr_keys[idempotency_key] = number
            return Ok(self._pr_ref(repo_url, number))

    def list_open_prs(self, repo_url: str) -> Result[tuple[PrSummary, ...], GitHubError]:
        with self._lock:
            if self._rate_limited:
                return self._rate_limit_error()
            repo = self._repos.get(repo_url)
            if repo is None:
                return self._unknown_repo_error(repo_url)
            summaries = tuple(
                PrSummary(
                    number=record.number,
                    head_sha=record.head_sha,
                    changed_files=tuple(file.path for file in record.files),
                )
                for record in sorted(repo.prs.values(), key=lambda record: record.number)
            )
            return Ok(summaries)

    def get_pr_files(
        self, repo_url: str, number: int
    ) -> Result[tuple[FileContent, ...], GitHubError]:
        with self._lock:
            if self._rate_limited:
                return self._rate_limit_error()
            repo = self._repos.get(repo_url)
            if repo is None:
                return self._unknown_repo_error(repo_url)
            record = repo.prs.get(number)
            if record is None:
                return self._unknown_pr_error(repo_url, number)
            return Ok(record.files)

    def post_comment(
        self, repo_url: str, number: int, body: str, idempotency_key: str
    ) -> Result[None, GitHubError]:
        with self._lock:
            if self._rate_limited:
                return self._rate_limit_error()
            repo = self._repos.get(repo_url)
            if repo is None:
                return self._unknown_repo_error(repo_url)
            if number not in repo.prs:
                return self._unknown_pr_error(repo_url, number)
            if not idempotency_key:
                return self._github_error("post_comment requires a non-empty idempotency_key")
            key = (number, idempotency_key)
            if key in repo.comment_keys:
                return Ok(None)
            repo.comment_keys.add(key)
            repo.comments.append(
                RecordedComment(pr_number=number, body=body, idempotency_key=idempotency_key)
            )
            return Ok(None)

    def recorded_comments(self, repo_url: str) -> tuple[RecordedComment, ...]:
        with self._lock:
            repo = self._repos.get(repo_url)
            if repo is None:
                return ()
            return tuple(repo.comments)

    def _pr_ref(self, repo_url: str, number: int) -> PullRequestRef:
        return PullRequestRef(number=number, url=f"{repo_url.rstrip('/')}/pull/{number}")

    def _rate_limit_error(self) -> Err[GitHubError]:
        error: GitHubError = RateLimitError("GitHub API rate limit exceeded")
        return Err(error)

    def _github_error(self, message: str) -> Err[GitHubError]:
        return Err(GitHubError(message))

    def _unknown_repo_error(self, repo_url: str) -> Err[GitHubError]:
        return Err(GitHubError("unknown repository", {"repo_url": repo_url}))

    def _unknown_pr_error(self, repo_url: str, number: int) -> Err[GitHubError]:
        return Err(
            GitHubError("unknown pull request", {"repo_url": repo_url, "number": str(number)})
        )


__all__ = ("FakeGitHubClient", "RecordedComment", "SeededPr")
