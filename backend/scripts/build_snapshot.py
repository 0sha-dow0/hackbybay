import hashlib
from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from backend.config import Settings
from backend.domain.enums import SandboxOutcome
from backend.domain.errors import Err, Ok, Result, SandboxError
from backend.ports.sandbox import SandboxCommand, SandboxHandle, SandboxRunner

_NPM_PROGRAM: Final[str] = "npm"
_NPM_INSTALL_SUBCOMMAND: Final[str] = "install"
_NPM_IGNORE_SCRIPTS_FLAG: Final[str] = "--ignore-scripts"
_INSTALL_ARGV: Final[tuple[str, ...]] = (
    _NPM_PROGRAM,
    _NPM_INSTALL_SUBCOMMAND,
    _NPM_IGNORE_SCRIPTS_FLAG,
)

_SANDBOX_ENV: Final[Mapping[str, str]] = MappingProxyType({})

_SNAPSHOT_ID_PREFIX: Final[str] = "depcover-snapshot"
_SNAPSHOT_ID_SEPARATOR: Final[str] = "-"
_DIGEST_FIELD_SEPARATOR: Final[str] = "\x00"
_DIGEST_ENCODING: Final[str] = "utf-8"
_DIGEST_LENGTH: Final[int] = 16

_EMPTY_REPO_PATH_MESSAGE: Final[str] = (
    "victim_repo_path must be a non-empty sandbox working directory"
)
_INSTALL_FAILED_MESSAGE: Final[str] = (
    "dependency install did not complete successfully in the sandbox"
)


def _install_command(victim_repo_path: str) -> SandboxCommand:
    return SandboxCommand(
        argv=_INSTALL_ARGV,
        cwd=victim_repo_path,
        env=_SANDBOX_ENV,
    )


def _install_dependencies(
    sandbox: SandboxRunner,
    settings: Settings,
    handle: SandboxHandle,
    victim_repo_path: str,
) -> Result[None, SandboxError]:
    result = sandbox.exec(
        handle,
        _install_command(victim_repo_path),
        settings.sandbox_exec_timeout_s,
    )
    if isinstance(result, Err):
        return result
    outcome = result.value.outcome
    if outcome is not SandboxOutcome.PASSED:
        return Err(
            SandboxError(
                _INSTALL_FAILED_MESSAGE,
                {"outcome": outcome.value, "stderr": result.value.stderr},
            )
        )
    return Ok(None)


def _derive_snapshot_id(base_snapshot_id: str, victim_repo_path: str) -> str:
    payload = f"{base_snapshot_id}{_DIGEST_FIELD_SEPARATOR}{victim_repo_path}"
    digest = hashlib.sha256(payload.encode(_DIGEST_ENCODING)).hexdigest()[:_DIGEST_LENGTH]
    return f"{_SNAPSHOT_ID_PREFIX}{_SNAPSHOT_ID_SEPARATOR}{digest}"


def build_snapshot(
    sandbox: SandboxRunner,
    settings: Settings,
    victim_repo_path: str,
) -> Result[str, SandboxError]:
    if victim_repo_path.strip() == "":
        return Err(
            SandboxError(
                _EMPTY_REPO_PATH_MESSAGE,
                {"victim_repo_path": victim_repo_path},
            )
        )
    acquired = sandbox.acquire(settings.daytona_snapshot_id)
    if isinstance(acquired, Err):
        return acquired
    handle = acquired.value
    installed = _install_dependencies(sandbox, settings, handle, victim_repo_path)
    released = sandbox.release(handle)
    if isinstance(installed, Err):
        return installed
    if isinstance(released, Err):
        return released
    return Ok(_derive_snapshot_id(settings.daytona_snapshot_id, victim_repo_path))


__all__ = ("build_snapshot",)
