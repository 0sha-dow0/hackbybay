from collections.abc import Sequence
from typing import TypeVar

from backend.domain.determinism import Clock
from backend.domain.enums import TERMINAL_INCIDENT_STATUSES, IncidentStatus, JudgeName
from backend.domain.errors import Err, Ok, RecordStoreError, Result
from backend.domain.models import (
    Incident,
    JudgeVerdict,
    Recipe,
    Repo,
    Review,
    Transplant,
    UnderwritingReport,
)
from backend.ports.record_store import RecordStore

_K = TypeVar("_K")
_V = TypeVar("_V")


def _insert_idempotent(
    store: dict[_K, _V], key: _K, value: _V, entity: str
) -> Result[_V, RecordStoreError]:
    if key not in store:
        store[key] = value
        return Ok(value)
    existing = store[key]
    if existing == value:
        return Ok(existing)
    return Err(
        RecordStoreError(
            f"{entity} already persisted with different content",
            {"entity": entity, "key": repr(key)},
        )
    )


def _fetch(store: dict[_K, _V], key: _K, entity: str) -> Result[_V, RecordStoreError]:
    if key not in store:
        return Err(
            RecordStoreError(f"{entity} not found", {"entity": entity, "key": repr(key)})
        )
    return Ok(store[key])


class FakeRecordStore(RecordStore):
    def __init__(self, clock: Clock) -> None:
        self._clock: Clock = clock
        self._repos: dict[str, Repo] = {}
        self._underwriting: dict[str, UnderwritingReport] = {}
        self._incidents: dict[str, Incident] = {}
        self._transplants: dict[str, Transplant] = {}
        self._verdicts: dict[tuple[str, JudgeName], JudgeVerdict] = {}
        self._reviews: dict[tuple[str, str], Review] = {}
        self._recipes: dict[str, Recipe] = {}

    def create_repo(self, repo: Repo) -> Result[Repo, RecordStoreError]:
        return _insert_idempotent(self._repos, repo.id, repo, "repo")

    def get_repo(self, repo_id: str) -> Result[Repo, RecordStoreError]:
        return _fetch(self._repos, repo_id, "repo")

    def save_underwriting(
        self, report: UnderwritingReport
    ) -> Result[UnderwritingReport, RecordStoreError]:
        return _insert_idempotent(
            self._underwriting, report.repo_id, report, "underwriting_report"
        )

    def get_underwriting(
        self, repo_id: str
    ) -> Result[UnderwritingReport, RecordStoreError]:
        return _fetch(self._underwriting, repo_id, "underwriting_report")

    def create_incident(self, incident: Incident) -> Result[Incident, RecordStoreError]:
        return _insert_idempotent(self._incidents, incident.id, incident, "incident")

    def get_incident(self, incident_id: str) -> Result[Incident, RecordStoreError]:
        return _fetch(self._incidents, incident_id, "incident")

    def update_incident(
        self, incident: Incident, expected_status: IncidentStatus
    ) -> Result[Incident, RecordStoreError]:
        if incident.id not in self._incidents:
            return Err(
                RecordStoreError(
                    "incident not found",
                    {"entity": "incident", "key": repr(incident.id)},
                )
            )
        current = self._incidents[incident.id]
        if current.status != expected_status:
            return Err(
                RecordStoreError(
                    "optimistic concurrency conflict on incident status",
                    {
                        "incident_id": incident.id,
                        "expected_status": expected_status.value,
                        "stored_status": current.status.value,
                    },
                )
            )
        if current.status in TERMINAL_INCIDENT_STATUSES:
            return Err(
                RecordStoreError(
                    "cannot update a terminal incident",
                    {"incident_id": incident.id, "stored_status": current.status.value},
                )
            )
        persisted = incident.model_copy(update={"updated_at": self._clock.now()})
        self._incidents[incident.id] = persisted
        return Ok(persisted)

    def save_transplant(
        self, transplant: Transplant
    ) -> Result[Transplant, RecordStoreError]:
        return _insert_idempotent(
            self._transplants, transplant.id, transplant, "transplant"
        )

    def get_transplant(
        self, transplant_id: str
    ) -> Result[Transplant, RecordStoreError]:
        return _fetch(self._transplants, transplant_id, "transplant")

    def save_verdicts(
        self, verdicts: Sequence[JudgeVerdict]
    ) -> Result[None, RecordStoreError]:
        staged: dict[tuple[str, JudgeName], JudgeVerdict] = {}
        for verdict in verdicts:
            key = (verdict.transplant_id, verdict.judge_name)
            existing = self._verdicts.get(key)
            if existing is not None and existing != verdict:
                return Err(
                    RecordStoreError(
                        "verdict conflicts with an already persisted verdict",
                        {
                            "transplant_id": verdict.transplant_id,
                            "judge_name": verdict.judge_name.value,
                        },
                    )
                )
            pending = staged.get(key)
            if pending is not None and pending != verdict:
                return Err(
                    RecordStoreError(
                        "batch contains conflicting verdicts for the same judge",
                        {
                            "transplant_id": verdict.transplant_id,
                            "judge_name": verdict.judge_name.value,
                        },
                    )
                )
            staged[key] = verdict
        self._verdicts.update(staged)
        return Ok(None)

    def save_review(self, review: Review) -> Result[Review, RecordStoreError]:
        return _insert_idempotent(
            self._reviews, (review.transplant_id, review.user_id), review, "review"
        )

    def upsert_recipe(self, recipe: Recipe) -> Result[Recipe, RecordStoreError]:
        self._recipes[recipe.library_pair] = recipe
        return Ok(recipe)

    def find_recipe(self, library_pair: str) -> Result[Recipe | None, RecordStoreError]:
        return Ok(self._recipes.get(library_pair))


__all__ = ("FakeRecordStore",)
