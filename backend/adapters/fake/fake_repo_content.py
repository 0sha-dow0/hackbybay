from __future__ import annotations

from collections.abc import Mapping
from pathlib import PurePosixPath

from backend.domain.errors import Err, IngestError, Ok, Result
from backend.domain.models import FileContent
from backend.ports.repo_content import RepoContentProvider

__all__ = ("FakeRepoContentProvider",)

_MANIFEST_FILENAME = "package.json"
_LOCKFILE_FILENAMES: tuple[str, ...] = (
    "package-lock.json",
    "npm-shrinkwrap.json",
    "yarn.lock",
    "pnpm-lock.yaml",
)
_PARENT_SEGMENT = ".."
_POSIX_SEPARATOR = "/"
_WINDOWS_SEPARATOR = "\\"
_CONTROL_CHARACTER_ORDINALS: frozenset[int] = frozenset(range(0x20)) | {0x7F}


def _contains_control_character(value: str) -> bool:
    """Reject NUL, CR, LF, every C0 control (< 0x20), and DEL (0x7F)."""
    return any(ord(character) in _CONTROL_CHARACTER_ORDINALS for character in value)


def _normalize_repo_relative_path(raw: str) -> str | None:
    if not raw:
        return None
    pure = PurePosixPath(raw.replace(_WINDOWS_SEPARATOR, _POSIX_SEPARATOR))
    if pure.is_absolute():
        return None
    parts = pure.parts
    if not parts or _PARENT_SEGMENT in parts:
        return None
    normalized = pure.as_posix()
    if _contains_control_character(normalized):
        return None
    return normalized


class FakeRepoContentProvider(RepoContentProvider):
    def __init__(self, repos: Mapping[str, tuple[FileContent, ...]]) -> None:
        for repo_url, files in repos.items():
            self._reject_duplicate_paths(repo_url, files)
        self._repos: dict[str, tuple[FileContent, ...]] = dict(repos)

    @staticmethod
    def _reject_duplicate_paths(
        repo_url: str, files: tuple[FileContent, ...]
    ) -> None:
        seen: set[str] = set()
        for file in files:
            if file.path in seen:
                raise ValueError(
                    f"repo {repo_url!r} fixture contains duplicate path {file.path!r}"
                )
            seen.add(file.path)

    def fetch(self, repo_url: str) -> Result[tuple[FileContent, ...], IngestError]:
        return self._resolve_files(repo_url)

    def read_manifest(self, repo_url: str) -> Result[FileContent, IngestError]:
        resolved = self._resolve_files(repo_url)
        if isinstance(resolved, Err):
            return resolved
        for file in resolved.value:
            if file.path == _MANIFEST_FILENAME:
                return Ok(file)
        return Err(
            IngestError(
                f"repo {repo_url!r} has no manifest {_MANIFEST_FILENAME!r}",
                {"repo_url": repo_url, "manifest": _MANIFEST_FILENAME},
            )
        )

    def read_lockfile(
        self, repo_url: str
    ) -> Result[FileContent | None, IngestError]:
        resolved = self._resolve_files(repo_url)
        if isinstance(resolved, Err):
            return resolved
        files_by_path = {file.path: file for file in resolved.value}
        for lockfile_name in _LOCKFILE_FILENAMES:
            lockfile = files_by_path.get(lockfile_name)
            if lockfile is not None:
                return Ok(lockfile)
        return Ok(None)

    def _resolve_files(
        self, repo_url: str
    ) -> Result[tuple[FileContent, ...], IngestError]:
        files = self._repos.get(repo_url)
        if files is None:
            return Err(
                IngestError(
                    f"unknown repo_url {repo_url!r}",
                    {"repo_url": repo_url},
                )
            )
        normalized_by_path: dict[str, FileContent] = {}
        for file in files:
            normalized_path = _normalize_repo_relative_path(file.path)
            if normalized_path is None:
                return Err(
                    IngestError(
                        f"repo {repo_url!r} contains invalid path {file.path!r}",
                        {"repo_url": repo_url, "path": file.path},
                    )
                )
            if normalized_path in normalized_by_path:
                return Err(
                    IngestError(
                        f"repo {repo_url!r} normalizes to duplicate path "
                        f"{normalized_path!r}",
                        {"repo_url": repo_url, "path": normalized_path},
                    )
                )
            normalized_by_path[normalized_path] = FileContent(
                path=normalized_path, text=file.text
            )
        ordered = tuple(normalized_by_path[path] for path in sorted(normalized_by_path))
        return Ok(ordered)
