import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from backend.config import load_settings
from backend.container import Container, build_container
from backend.domain.constants import TARGET_PACKAGE
from backend.domain.enums import IncidentStatus, StrategyKind, TriggerType
from backend.domain.errors import Err, Ok
from backend.domain.models import (
    FileContent,
    FileDecision,
    Incident,
    Repo,
    Transplant,
)
from backend.ports.auth import AuthenticatedUser
from backend.routers.deps import http_error, require_user
from backend.services.incident_state import transition

load_dotenv(Path(__file__).resolve().parent / ".env")

_STATIC = Path(__file__).resolve().parent / "static"
_CVE_SUMMARY = "CVE-2026-0001: axios request handling flaw enables SSRF on unvalidated redirects."


def _bootstrap_container() -> Container:
    settings_result = load_settings()
    if isinstance(settings_result, Err):
        raise RuntimeError(f"config error: {settings_result.error}")
    built = build_container(settings_result.value)
    if isinstance(built, Err):
        raise RuntimeError(f"container error: {built.error}")
    return built.value


class RegisterRepoRequest(BaseModel):
    url: str
    owner: str


class FireIncidentRequest(BaseModel):
    repo_id: str


class ChooseStrategyRequest(BaseModel):
    strategy: StrategyKind


class FileDecisionIn(BaseModel):
    path: str
    kind: str
    reason: str | None = None


class ReviewRequest(BaseModel):
    decision: str
    per_file: list[FileDecisionIn]
    reason: str | None = None


def create_app(container: Container) -> FastAPI:
    app = FastAPI(title="DepCover")
    pipeline_tasks: set[asyncio.Task[Any]] = set()
    user_dep = require_user(container)

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "use_fakes": container.settings.use_fakes}

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(_STATIC / "index.html")

    @app.post("/repos")
    def register_repo(
        body: RegisterRepoRequest, user: AuthenticatedUser = Depends(user_dep)
    ) -> JSONResponse:
        repo = Repo(
            id=container.ids.new_id("repo"),
            url=body.url,
            owner=body.owner,
            registered_at=container.clock.now(),
        )
        scan = container.ingestion.scan(repo, TARGET_PACKAGE)
        if isinstance(scan, Err):
            raise http_error(scan.error)
        surgery_plan, layout, centrality, warnings = scan.value
        underwriting = container.underwriter.run(repo, surgery_plan, centrality, layout, warnings)
        if isinstance(underwriting, Err):
            raise http_error(underwriting.error)
        return JSONResponse(
            {
                "repo": repo.model_dump(mode="json"),
                "surgery_plan": surgery_plan.model_dump(mode="json"),
                "graph_layout": layout.model_dump(mode="json"),
                "underwriting": underwriting.value.model_dump(mode="json"),
            }
        )

    @app.post("/incidents")
    def fire_incident(
        body: FireIncidentRequest, user: AuthenticatedUser = Depends(user_dep)
    ) -> JSONResponse:
        now = container.clock.now()
        incident = Incident(
            id=container.ids.new_id("incident"),
            repo_id=body.repo_id,
            trigger_type=TriggerType.MOCK_CVE,
            chosen_strategy=None,
            status=IncidentStatus.PENDING,
            created_at=now,
            updated_at=now,
        )
        created = container.store.create_incident(incident)
        if isinstance(created, Err):
            raise http_error(created.error)
        underwriting = container.store.get_underwriting(body.repo_id)
        if isinstance(underwriting, Err):
            raise http_error(underwriting.error)
        options = container.mitigation.options(incident.id, underwriting.value, _CVE_SUMMARY)
        if isinstance(options, Err):
            raise http_error(options.error)
        return JSONResponse(
            {
                "incident": created.value.model_dump(mode="json"),
                "options": options.value.model_dump(mode="json"),
            }
        )

    @app.post("/incidents/{incident_id}/strategy")
    def choose_strategy(
        incident_id: str,
        body: ChooseStrategyRequest,
        user: AuthenticatedUser = Depends(user_dep),
    ) -> JSONResponse:
        loaded = container.store.get_incident(incident_id)
        if isinstance(loaded, Err):
            raise http_error(loaded.error)
        incident = loaded.value
        chosen = incident.model_copy(update={"chosen_strategy": body.strategy})
        if body.strategy is not StrategyKind.TRANSPLANT:
            terminal = transition(chosen, IncidentStatus.COMPLETED, container.clock.now())
            if isinstance(terminal, Err):
                raise http_error(terminal.error)
            saved = container.store.update_incident(terminal.value, incident.status)
            if isinstance(saved, Err):
                raise http_error(saved.error)
            return JSONResponse(saved.value.model_dump(mode="json"))

        saved = container.store.update_incident(chosen, incident.status)
        if isinstance(saved, Err):
            raise http_error(saved.error)
        repo = container.store.get_repo(incident.repo_id)
        if isinstance(repo, Err):
            raise http_error(repo.error)
        scan = container.ingestion.scan(repo.value, TARGET_PACKAGE)
        if isinstance(scan, Err):
            raise http_error(scan.error)
        surgery_plan, _layout, _centrality, _warnings = scan.value
        files = container.repos_provider.fetch(repo.value.url)
        if isinstance(files, Err):
            raise http_error(files.error)
        by_path: dict[str, FileContent] = {f.path: f for f in files.value}
        affected = tuple(by_path[p] for p in surgery_plan.affected_files if p in by_path)
        task = asyncio.create_task(
            container.orchestrator.run(saved.value, surgery_plan, affected, container.golden)
        )
        pipeline_tasks.add(task)
        task.add_done_callback(pipeline_tasks.discard)
        return JSONResponse(saved.value.model_dump(mode="json"))

    @app.get("/incidents/{incident_id}/stream")
    async def stream(incident_id: str) -> EventSourceResponse:
        async def generator() -> AsyncIterator[dict[str, str]]:
            seen: set[int] = set()
            replay = container.events.replay(incident_id)
            if isinstance(replay, Ok):
                for event in replay.value:
                    seen.add(event.seq)
                    yield {"data": event.model_dump_json()}
                    if event.terminal:
                        return
            async for event in container.events.subscribe(incident_id):
                if event.seq in seen:
                    continue
                yield {"data": event.model_dump_json()}
                if event.terminal:
                    return

        return EventSourceResponse(generator())

    @app.get("/transplants/{transplant_id}")
    def get_transplant(
        transplant_id: str, user: AuthenticatedUser = Depends(user_dep)
    ) -> JSONResponse:
        result = container.store.get_transplant(transplant_id)
        if isinstance(result, Err):
            raise http_error(result.error)
        return JSONResponse(result.value.model_dump(mode="json"))

    @app.post("/transplants/{transplant_id}/review")
    def submit_review(
        transplant_id: str,
        body: ReviewRequest,
        user: AuthenticatedUser = Depends(user_dep),
    ) -> JSONResponse:
        loaded = container.store.get_transplant(transplant_id)
        if isinstance(loaded, Err):
            raise http_error(loaded.error)
        transplant: Transplant = loaded.value
        decision = _parse_decision(body.decision)
        per_file = tuple(
            FileDecision(path=d.path, kind=_parse_file_kind(d.kind), reason=d.reason)
            for d in body.per_file
        )
        submitted = container.review.submit(user, transplant, decision, per_file)
        if isinstance(submitted, Err):
            raise http_error(submitted.error)
        review, status = submitted.value
        pull_request: dict[str, Any] | None = None
        if status is IncidentStatus.COMPLETED:
            incident = container.store.get_incident(transplant.incident_id)
            if isinstance(incident, Ok):
                repo = container.store.get_repo(incident.value.repo_id)
                if isinstance(repo, Ok):
                    opened = container.pr.open_for(repo.value, transplant, review)
                    if isinstance(opened, Ok):
                        pull_request = opened.value.model_dump(mode="json")
        return JSONResponse(
            {
                "review": review.model_dump(mode="json"),
                "status": status.value,
                "pull_request": pull_request,
            }
        )

    return app


def _parse_decision(raw: str) -> Any:
    from backend.domain.enums import ReviewDecision

    return ReviewDecision(raw)


def _parse_file_kind(raw: str) -> Any:
    from backend.domain.enums import FileDecisionKind

    return FileDecisionKind(raw)


container_instance = _bootstrap_container()
app = create_app(container_instance)


@app.exception_handler(Exception)
async def unhandled(_request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=500, content={"code": "internal_error", "message": str(exc)})
