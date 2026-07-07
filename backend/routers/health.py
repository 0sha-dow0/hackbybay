from __future__ import annotations

import os
from collections.abc import Iterable

from fastapi import APIRouter, Response, status

from backend.config import Settings, load_settings
from backend.domain.errors import Err

router = APIRouter()

_OK = "ok"
_ERROR = "error"
_SKIPPED = "skipped"


def _present(value: str | None) -> bool:
    return value is not None and value.strip() != ""


def _secret_env_status(env_name: str | None) -> str:
    if not _present(env_name):
        return "missing env pointer"
    if _present(os.environ.get(env_name or "")):
        return _OK
    return f"missing secret env {env_name}"


def _combined_status(statuses: Iterable[str]) -> str:
    problems = [item for item in statuses if item != _OK]
    if not problems:
        return _OK
    return "; ".join(problems)


def _live_service_checks(settings: Settings) -> dict[str, str]:
    checks: dict[str, str] = {}
    checks["neo4j"] = _combined_status(
        (
            _OK if _present(settings.neo4j_uri) else "missing DEPCOVER_NEO4J_URI",
            _OK if _present(settings.neo4j_user) else "missing DEPCOVER_NEO4J_USER",
            _secret_env_status(settings.neo4j_password_env),
        )
    )
    checks["butterbase"] = _combined_status(
        (
            _OK
            if _present(settings.butterbase_base_url)
            else "missing DEPCOVER_BUTTERBASE_BASE_URL",
            _secret_env_status(settings.butterbase_key_env),
        )
    )
    missing_llm_keys = [
        f"{role.value}:{config.api_key_env}"
        for role, config in sorted(
            settings.llm_roles.items(), key=lambda item: item[0].value
        )
        if not _present(os.environ.get(config.api_key_env))
    ]
    checks["llm_api_keys"] = (
        _OK if not missing_llm_keys else f"missing {', '.join(missing_llm_keys)}"
    )
    if settings.github_token_env is not None:
        checks["github_token"] = _secret_env_status(settings.github_token_env)
    return checks


@router.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": _OK}


@router.get("/ready", tags=["system"])
def ready(response: Response) -> dict[str, object]:
    loaded = load_settings()
    if isinstance(loaded, Err):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": _ERROR,
            "mode": "unknown",
            "checks": {"settings": loaded.error.message},
        }

    settings = loaded.value
    checks = {"settings": _OK}
    mode = "fake" if settings.use_fakes else "live"
    if settings.use_fakes:
        checks["live_services"] = _SKIPPED
    else:
        checks.update(_live_service_checks(settings))

    if any(value != _OK and value != _SKIPPED for value in checks.values()):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        overall = _ERROR
    else:
        overall = _OK

    return {"status": overall, "mode": mode, "checks": checks}
