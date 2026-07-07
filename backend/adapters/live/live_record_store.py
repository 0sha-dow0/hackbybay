from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Final, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from backend.domain.enums import IncidentStatus, JudgeName
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

_AUTO_API_SEGMENT: Final = "auto-api"
_EQ_PREFIX: Final = "eq."

_METHOD_GET: Final = "GET"
_METHOD_POST: Final = "POST"
_METHOD_PATCH: Final = "PATCH"

_HEADER_AUTHORIZATION: Final = "Authorization"
_HEADER_APIKEY: Final = "apikey"
_HEADER_CONTENT_TYPE: Final = "Content-Type"
_HEADER_PREFER: Final = "Prefer"
_BEARER_PREFIX: Final = "Bearer "
_CONTENT_TYPE_JSON: Final = "application/json"
_PREFER_REPRESENTATION: Final = "return=representation"

_TABLE_REPOS: Final = "repos"
_TABLE_UNDERWRITING: Final = "underwriting_reports"
_TABLE_INCIDENTS: Final = "incidents"
_TABLE_TRANSPLANTS: Final = "transplants"
_TABLE_VERDICTS: Final = "judge_verdicts"
_TABLE_REVIEWS: Final = "reviews"
_TABLE_RECIPES: Final = "recipes"

_COL_ID: Final = "id"
_COL_REPO_ID: Final = "repo_id"
_COL_TRANSPLANT_ID: Final = "transplant_id"
_COL_USER_ID: Final = "user_id"
_COL_JUDGE_NAME: Final = "judge_name"
_COL_LIBRARY_PAIR: Final = "library_pair"
_COL_STATUS: Final = "status"
_COL_UPDATED_AT: Final = "updated_at"
_COL_CHOSEN_STRATEGY: Final = "chosen_strategy"

_TIMEOUT_MESSAGE: Final = "butterbase request timed out"
_TRANSPORT_MESSAGE: Final = "butterbase transport failure"
_STATUS_MESSAGE: Final = "butterbase returned an unexpected status"
_MALFORMED_MESSAGE: Final = "butterbase returned a malformed response"
_NOT_FOUND_MESSAGE: Final = "record not found"
_CONFLICT_MESSAGE: Final = "record already persisted with different content"
_STALE_MESSAGE: Final = "incident is stale or terminal"
_BATCH_CONFLICT_MESSAGE: Final = "batch contains conflicting verdicts for the same judge"

_ModelT = TypeVar("_ModelT", bound=BaseModel)


class LiveRecordStore(RecordStore):
    def __init__(self, base_url: str, service_key: str, timeout_s: float = 15.0) -> None:
        self._base_url: str = base_url
        self._service_key: str = service_key
        self._timeout_s: float = timeout_s

    def _table_url(self, table: str) -> str:
        return f"{self._base_url.rstrip('/')}/{_AUTO_API_SEGMENT}/{table}"

    def _service_headers(self) -> dict[str, str]:
        return {
            _HEADER_AUTHORIZATION: f"{_BEARER_PREFIX}{self._service_key}",
            _HEADER_APIKEY: self._service_key,
            _HEADER_CONTENT_TYPE: _CONTENT_TYPE_JSON,
            _HEADER_PREFER: _PREFER_REPRESENTATION,
        }

    @staticmethod
    def _eq_filters(filters: Mapping[str, str]) -> dict[str, str]:
        return {column: f"{_EQ_PREFIX}{value}" for column, value in filters.items()}

    def _execute(
        self,
        method: str,
        table: str,
        *,
        params: Mapping[str, str] | None,
        json_body: object | None,
    ) -> Result[list[object], RecordStoreError]:
        try:
            with httpx.Client(timeout=self._timeout_s) as client:
                response = client.request(
                    method,
                    self._table_url(table),
                    headers=self._service_headers(),
                    params=params,
                    json=json_body,
                )
        except httpx.TimeoutException:
            return Err(RecordStoreError(_TIMEOUT_MESSAGE, {"table": table}))
        except httpx.HTTPError:
            return Err(RecordStoreError(_TRANSPORT_MESSAGE, {"table": table}))
        if not response.is_success:
            return Err(
                RecordStoreError(
                    _STATUS_MESSAGE,
                    {"status": str(response.status_code), "table": table},
                )
            )
        try:
            parsed: object = response.json()
        except json.JSONDecodeError:
            return Err(RecordStoreError(_MALFORMED_MESSAGE, {"table": table}))
        if not isinstance(parsed, list):
            return Err(RecordStoreError(_MALFORMED_MESSAGE, {"table": table}))
        rows: list[object] = list(parsed)
        return Ok(rows)

    def _query(
        self, table: str, filters: Mapping[str, str]
    ) -> Result[list[object], RecordStoreError]:
        return self._execute(
            _METHOD_GET, table, params=self._eq_filters(filters), json_body=None
        )

    def _insert(
        self, table: str, body: object
    ) -> Result[list[object], RecordStoreError]:
        return self._execute(_METHOD_POST, table, params=None, json_body=body)

    def _patch(
        self, table: str, filters: Mapping[str, str], body: object
    ) -> Result[list[object], RecordStoreError]:
        return self._execute(
            _METHOD_PATCH, table, params=self._eq_filters(filters), json_body=body
        )

    def _parse_row(
        self, table: str, model_cls: type[_ModelT], row: object
    ) -> Result[_ModelT, RecordStoreError]:
        try:
            return Ok(model_cls.model_validate(row))
        except ValidationError:
            return Err(RecordStoreError(_MALFORMED_MESSAGE, {"table": table}))

    def _save_idempotent(
        self,
        table: str,
        model_cls: type[_ModelT],
        filters: Mapping[str, str],
        model: _ModelT,
    ) -> Result[_ModelT, RecordStoreError]:
        existing = self._query(table, filters)
        if isinstance(existing, Err):
            return existing
        rows = existing.value
        if rows:
            parsed = self._parse_row(table, model_cls, rows[0])
            if isinstance(parsed, Err):
                return parsed
            if parsed.value == model:
                return Ok(parsed.value)
            return Err(RecordStoreError(_CONFLICT_MESSAGE, {"table": table}))
        inserted = self._insert(table, model.model_dump(mode="json"))
        if isinstance(inserted, Err):
            return inserted
        inserted_rows = inserted.value
        if not inserted_rows:
            return Err(RecordStoreError(_MALFORMED_MESSAGE, {"table": table}))
        return self._parse_row(table, model_cls, inserted_rows[0])

    def _get_one(
        self, table: str, model_cls: type[_ModelT], filters: Mapping[str, str]
    ) -> Result[_ModelT, RecordStoreError]:
        existing = self._query(table, filters)
        if isinstance(existing, Err):
            return existing
        rows = existing.value
        if not rows:
            return Err(RecordStoreError(_NOT_FOUND_MESSAGE, {"table": table}))
        return self._parse_row(table, model_cls, rows[0])

    def create_repo(self, repo: Repo) -> Result[Repo, RecordStoreError]:
        return self._save_idempotent(_TABLE_REPOS, Repo, {_COL_ID: repo.id}, repo)

    def get_repo(self, repo_id: str) -> Result[Repo, RecordStoreError]:
        return self._get_one(_TABLE_REPOS, Repo, {_COL_ID: repo_id})

    def save_underwriting(
        self, report: UnderwritingReport
    ) -> Result[UnderwritingReport, RecordStoreError]:
        return self._save_idempotent(
            _TABLE_UNDERWRITING,
            UnderwritingReport,
            {_COL_REPO_ID: report.repo_id},
            report,
        )

    def get_underwriting(
        self, repo_id: str
    ) -> Result[UnderwritingReport, RecordStoreError]:
        return self._get_one(
            _TABLE_UNDERWRITING, UnderwritingReport, {_COL_REPO_ID: repo_id}
        )

    def create_incident(self, incident: Incident) -> Result[Incident, RecordStoreError]:
        return self._save_idempotent(
            _TABLE_INCIDENTS, Incident, {_COL_ID: incident.id}, incident
        )

    def get_incident(self, incident_id: str) -> Result[Incident, RecordStoreError]:
        return self._get_one(_TABLE_INCIDENTS, Incident, {_COL_ID: incident_id})

    def update_incident(
        self, incident: Incident, expected_status: IncidentStatus
    ) -> Result[Incident, RecordStoreError]:
        dumped = incident.model_dump(mode="json")
        body: dict[str, object] = {
            _COL_STATUS: dumped[_COL_STATUS],
            _COL_UPDATED_AT: dumped[_COL_UPDATED_AT],
            _COL_CHOSEN_STRATEGY: dumped[_COL_CHOSEN_STRATEGY],
        }
        filters = {_COL_ID: incident.id, _COL_STATUS: expected_status.value}
        updated = self._patch(_TABLE_INCIDENTS, filters, body)
        if isinstance(updated, Err):
            return updated
        rows = updated.value
        if not rows:
            return Err(RecordStoreError(_STALE_MESSAGE, {"table": _TABLE_INCIDENTS}))
        return self._parse_row(_TABLE_INCIDENTS, Incident, rows[0])

    def save_transplant(
        self, transplant: Transplant
    ) -> Result[Transplant, RecordStoreError]:
        return self._save_idempotent(
            _TABLE_TRANSPLANTS, Transplant, {_COL_ID: transplant.id}, transplant
        )

    def get_transplant(
        self, transplant_id: str
    ) -> Result[Transplant, RecordStoreError]:
        return self._get_one(
            _TABLE_TRANSPLANTS, Transplant, {_COL_ID: transplant_id}
        )

    def save_verdicts(
        self, verdicts: Sequence[JudgeVerdict]
    ) -> Result[None, RecordStoreError]:
        if not verdicts:
            return Ok(None)
        staged: dict[tuple[str, JudgeName], JudgeVerdict] = {}
        for verdict in verdicts:
            key = (verdict.transplant_id, verdict.judge_name)
            pending = staged.get(key)
            if pending is not None and pending != verdict:
                return Err(
                    RecordStoreError(
                        _BATCH_CONFLICT_MESSAGE, {"table": _TABLE_VERDICTS}
                    )
                )
            staged[key] = verdict
        to_insert: list[JudgeVerdict] = []
        for (transplant_id, judge_name), verdict in staged.items():
            filters = {
                _COL_TRANSPLANT_ID: transplant_id,
                _COL_JUDGE_NAME: judge_name.value,
            }
            existing = self._query(_TABLE_VERDICTS, filters)
            if isinstance(existing, Err):
                return existing
            rows = existing.value
            if not rows:
                to_insert.append(verdict)
                continue
            parsed = self._parse_row(_TABLE_VERDICTS, JudgeVerdict, rows[0])
            if isinstance(parsed, Err):
                return parsed
            if parsed.value != verdict:
                return Err(
                    RecordStoreError(_CONFLICT_MESSAGE, {"table": _TABLE_VERDICTS})
                )
        if not to_insert:
            return Ok(None)
        body = [verdict.model_dump(mode="json") for verdict in to_insert]
        inserted = self._insert(_TABLE_VERDICTS, body)
        if isinstance(inserted, Err):
            return inserted
        return Ok(None)

    def save_review(self, review: Review) -> Result[Review, RecordStoreError]:
        return self._save_idempotent(
            _TABLE_REVIEWS,
            Review,
            {
                _COL_TRANSPLANT_ID: review.transplant_id,
                _COL_USER_ID: review.user_id,
            },
            review,
        )

    def upsert_recipe(self, recipe: Recipe) -> Result[Recipe, RecordStoreError]:
        existing = self._query(_TABLE_RECIPES, {_COL_LIBRARY_PAIR: recipe.library_pair})
        if isinstance(existing, Err):
            return existing
        body = recipe.model_dump(mode="json")
        if existing.value:
            written = self._patch(
                _TABLE_RECIPES, {_COL_LIBRARY_PAIR: recipe.library_pair}, body
            )
        else:
            written = self._insert(_TABLE_RECIPES, body)
        if isinstance(written, Err):
            return written
        rows = written.value
        if not rows:
            return Err(RecordStoreError(_MALFORMED_MESSAGE, {"table": _TABLE_RECIPES}))
        return self._parse_row(_TABLE_RECIPES, Recipe, rows[0])

    def find_recipe(self, library_pair: str) -> Result[Recipe | None, RecordStoreError]:
        existing = self._query(_TABLE_RECIPES, {_COL_LIBRARY_PAIR: library_pair})
        if isinstance(existing, Err):
            return existing
        rows = existing.value
        if not rows:
            return Ok(None)
        parsed = self._parse_row(_TABLE_RECIPES, Recipe, rows[0])
        if isinstance(parsed, Err):
            return parsed
        found: Recipe | None = parsed.value
        return Ok(found)


__all__ = ("LiveRecordStore",)
