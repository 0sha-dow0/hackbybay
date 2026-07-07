from collections.abc import Sequence
from typing import Protocol

from backend.domain.enums import IncidentStatus
from backend.domain.errors import RecordStoreError, Result
from backend.domain.models import (
    Incident,
    JudgeVerdict,
    Recipe,
    Repo,
    Review,
    Transplant,
    UnderwritingReport,
)


class RecordStore(Protocol):
    def create_repo(self, repo: Repo) -> Result[Repo, RecordStoreError]: ...

    def get_repo(self, repo_id: str) -> Result[Repo, RecordStoreError]: ...

    def save_underwriting(
        self, report: UnderwritingReport
    ) -> Result[UnderwritingReport, RecordStoreError]: ...

    def get_underwriting(
        self, repo_id: str
    ) -> Result[UnderwritingReport, RecordStoreError]: ...

    def create_incident(self, incident: Incident) -> Result[Incident, RecordStoreError]: ...

    def get_incident(self, incident_id: str) -> Result[Incident, RecordStoreError]: ...

    def update_incident(
        self, incident: Incident, expected_status: IncidentStatus
    ) -> Result[Incident, RecordStoreError]: ...

    def save_transplant(
        self, transplant: Transplant
    ) -> Result[Transplant, RecordStoreError]: ...

    def get_transplant(
        self, transplant_id: str
    ) -> Result[Transplant, RecordStoreError]: ...

    def save_verdicts(
        self, verdicts: Sequence[JudgeVerdict]
    ) -> Result[None, RecordStoreError]: ...

    def save_review(self, review: Review) -> Result[Review, RecordStoreError]: ...

    def upsert_recipe(self, recipe: Recipe) -> Result[Recipe, RecordStoreError]: ...

    def find_recipe(self, library_pair: str) -> Result[Recipe | None, RecordStoreError]: ...


__all__ = ("RecordStore",)
