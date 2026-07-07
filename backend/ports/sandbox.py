import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Protocol

from backend.domain.enums import SandboxOutcome
from backend.domain.errors import Err, Ok, Result, SandboxError


@dataclass(frozen=True)
class SandboxHandle:
    id: str


@dataclass(frozen=True)
class SandboxCommand:
    argv: tuple[str, ...]
    cwd: str
    env: Mapping[str, str]


@dataclass(frozen=True)
class SandboxResult:
    outcome: SandboxOutcome
    exit_code: int | None
    stdout: str
    stderr: str
    duration_s: float


class SandboxRunner(Protocol):
    def acquire(self, snapshot_id: str) -> Result[SandboxHandle, SandboxError]: ...

    def write_files(
        self, h: SandboxHandle, files: Mapping[str, str]
    ) -> Result[None, SandboxError]: ...

    def exec(
        self, h: SandboxHandle, cmd: SandboxCommand, timeout_s: float
    ) -> Result[SandboxResult, SandboxError]: ...

    def release(self, h: SandboxHandle) -> Result[None, SandboxError]: ...


SECRET_KEY_SUBSTRINGS: Final[frozenset[str]] = frozenset(
    {"key", "token", "secret", "password", "credential", "api"}
)
PATH_SEPARATOR: Final[str] = "/"
PARENT_DIR_COMPONENT: Final[str] = ".."


def find_secret_env_key(env: Mapping[str, str]) -> str | None:
    for name in sorted(env):
        folded = name.casefold()
        for substring in SECRET_KEY_SUBSTRINGS:
            if substring in folded:
                return name
    return None


def validate_command(cmd: SandboxCommand) -> Result[None, SandboxError]:
    if len(cmd.argv) == 0:
        return Err(
            SandboxError(
                "sandbox command argv must not be empty",
                {"cwd": cmd.cwd},
            )
        )
    offending = find_secret_env_key(cmd.env)
    if offending is not None:
        return Err(
            SandboxError(
                "sandbox command env contains a forbidden secret-like key",
                {"key": offending},
            )
        )
    return Ok(None)


def validate_exec_timeout(timeout_s: float) -> Result[None, SandboxError]:
    if not math.isfinite(timeout_s) or timeout_s <= 0.0:
        return Err(
            SandboxError(
                "sandbox exec requires a finite positive timeout_s",
                {"timeout_s": repr(timeout_s)},
            )
        )
    return Ok(None)


def validate_sandbox_path(path: str) -> Result[None, SandboxError]:
    if path == "":
        return Err(SandboxError("sandbox file path must not be empty", {"path": path}))
    if path.startswith(PATH_SEPARATOR):
        return Err(
            SandboxError(
                "sandbox file path must be repo-relative, not absolute",
                {"path": path},
            )
        )
    if PARENT_DIR_COMPONENT in path.split(PATH_SEPARATOR):
        return Err(
            SandboxError(
                "sandbox file path must not traverse parent directories",
                {"path": path},
            )
        )
    return Ok(None)
