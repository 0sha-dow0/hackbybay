from collections.abc import Awaitable, Callable

from fastapi import HTTPException, Request

from backend.container import Container
from backend.domain.errors import DepCoverError, Err
from backend.ports.auth import AuthenticatedUser

_CODE_STATUS: dict[str, int] = {
    "config_error": 500,
    "ingest_error": 400,
    "lockfile_parse_error": 400,
    "graph_error": 500,
    "sandbox_error": 500,
    "sandbox_timeout_error": 504,
    "sandbox_unavailable_error": 503,
    "llm_error": 502,
    "llm_timeout_error": 504,
    "llm_malformed_output_error": 502,
    "validation_rejected_error": 422,
    "record_store_error": 500,
    "auth_error": 401,
    "github_error": 502,
    "rate_limit_error": 429,
    "state_transition_error": 409,
    "depcover_error": 500,
}

_BEARER_PREFIX = "Bearer "


def http_error(error: DepCoverError) -> HTTPException:
    status = _CODE_STATUS.get(error.code, 500)
    return HTTPException(status_code=status, detail={"code": error.code, "message": error.message})


def require_user(container: Container) -> Callable[[Request], Awaitable[AuthenticatedUser]]:
    async def dependency(request: Request) -> AuthenticatedUser:
        header = request.headers.get("Authorization", "")
        token = header[len(_BEARER_PREFIX) :] if header.startswith(_BEARER_PREFIX) else ""
        result = container.auth.verify(token)
        if isinstance(result, Err):
            raise http_error(result.error)
        return result.value

    return dependency
