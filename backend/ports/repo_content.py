from __future__ import annotations

from typing import Protocol

from backend.domain.errors import IngestError, Result
from backend.domain.models import FileContent

__all__ = ("RepoContentProvider",)


class RepoContentProvider(Protocol):
    def fetch(self, repo_url: str) -> Result[tuple[FileContent, ...], IngestError]: ...

    def read_manifest(self, repo_url: str) -> Result[FileContent, IngestError]: ...

    def read_lockfile(
        self, repo_url: str
    ) -> Result[FileContent | None, IngestError]: ...
