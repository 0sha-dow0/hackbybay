from collections.abc import Mapping
from typing import Final

from backend.domain.enums import SandboxOutcome
from backend.domain.errors import (
    Err,
    Ok,
    Result,
    SandboxError,
    SandboxUnavailableError,
)
from backend.ports.sandbox import (
    SandboxCommand,
    SandboxHandle,
    SandboxResult,
    SandboxRunner,
    validate_command,
    validate_exec_timeout,
    validate_sandbox_path,
)

_DEFAULT_CAPACITY: Final[int] = 1
_HANDLE_ID_PREFIX: Final[str] = "fake-sandbox"


def _validated_script(
    scripted: Mapping[tuple[str, ...], SandboxResult],
) -> dict[tuple[str, ...], SandboxResult]:
    for argv, result in scripted.items():
        if len(argv) == 0:
            raise ValueError("scripted sandbox argv key must not be empty")
        if result.outcome is SandboxOutcome.TIMEOUT and result.exit_code is not None:
            raise ValueError(
                "scripted TIMEOUT result must have exit_code=None for argv "
                + repr(argv)
            )
    return dict(scripted)


class FakeSandbox(SandboxRunner):
    def __init__(
        self,
        scripted: Mapping[tuple[str, ...], SandboxResult],
        *,
        capacity: int = _DEFAULT_CAPACITY,
    ) -> None:
        if capacity < 0:
            raise ValueError("FakeSandbox capacity must be non-negative")
        self._scripted: dict[tuple[str, ...], SandboxResult] = _validated_script(
            scripted
        )
        self._capacity: int = capacity
        self._active: set[str] = set()
        self._acquire_count: int = 0

    def acquire(self, snapshot_id: str) -> Result[SandboxHandle, SandboxError]:
        if snapshot_id.strip() == "":
            return Err(
                SandboxError(
                    "sandbox snapshot_id must not be empty",
                    {"snapshot_id": snapshot_id},
                )
            )
        if len(self._active) >= self._capacity:
            return Err(
                SandboxUnavailableError(
                    "no sandbox available to acquire",
                    {"snapshot_id": snapshot_id},
                )
            )
        self._acquire_count += 1
        handle_id = f"{_HANDLE_ID_PREFIX}-{self._acquire_count}"
        self._active.add(handle_id)
        return Ok(SandboxHandle(handle_id))

    def write_files(
        self, h: SandboxHandle, files: Mapping[str, str]
    ) -> Result[None, SandboxError]:
        liveness = self._require_active(h)
        if isinstance(liveness, Err):
            return liveness
        for path in sorted(files):
            path_check = validate_sandbox_path(path)
            if isinstance(path_check, Err):
                return path_check
        return Ok(None)

    def exec(
        self, h: SandboxHandle, cmd: SandboxCommand, timeout_s: float
    ) -> Result[SandboxResult, SandboxError]:
        liveness = self._require_active(h)
        if isinstance(liveness, Err):
            return liveness
        timeout_check = validate_exec_timeout(timeout_s)
        if isinstance(timeout_check, Err):
            return timeout_check
        command_check = validate_command(cmd)
        if isinstance(command_check, Err):
            return command_check
        scripted = self._scripted.get(cmd.argv)
        if scripted is None:
            return Err(
                SandboxError(
                    "no scripted sandbox result for argv",
                    {"argv": repr(cmd.argv)},
                )
            )
        return Ok(scripted)

    def release(self, h: SandboxHandle) -> Result[None, SandboxError]:
        liveness = self._require_active(h)
        if isinstance(liveness, Err):
            return liveness
        self._active.discard(h.id)
        return Ok(None)

    def _require_active(self, h: SandboxHandle) -> Result[None, SandboxError]:
        if h.id not in self._active:
            return Err(
                SandboxUnavailableError(
                    "sandbox handle is not active",
                    {"handle_id": h.id},
                )
            )
        return Ok(None)
