from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from backend.config import Settings
from backend.domain.enums import SandboxOutcome
from backend.domain.errors import DepCoverError, Err, Ok, Result
from backend.domain.models import BehavioralCase, NormalizedOutput
from backend.ports.sandbox import SandboxCommand, SandboxHandle, SandboxRunner
from backend.services.battery import input_battery
from backend.services.normalizer import normalize_output

_HARNESS_PROGRAM: Final[str] = "node"
_HARNESS_ENTRYPOINT: Final[str] = "harness.js"
_HARNESS_CWD: Final[str] = "."
_HARNESS_ENV: Final[Mapping[str, str]] = MappingProxyType({})

_HARNESS_FAILED_MESSAGE: Final[str] = (
    "behavioral harness did not complete successfully in the sandbox"
)


def _harness_command(case_id: str) -> SandboxCommand:
    return SandboxCommand(
        argv=(_HARNESS_PROGRAM, _HARNESS_ENTRYPOINT, case_id),
        cwd=_HARNESS_CWD,
        env=_HARNESS_ENV,
    )


def _record_case(
    sandbox: SandboxRunner,
    settings: Settings,
    handle: SandboxHandle,
    case: BehavioralCase,
) -> Result[NormalizedOutput, DepCoverError]:
    result = sandbox.exec(
        handle,
        _harness_command(case.id),
        settings.sandbox_exec_timeout_s,
    )
    if isinstance(result, Err):
        return Err(result.error)
    outcome = result.value.outcome
    if outcome is not SandboxOutcome.PASSED:
        return Err(
            DepCoverError(
                _HARNESS_FAILED_MESSAGE,
                {
                    "case_id": case.id,
                    "outcome": outcome.value,
                    "stderr": result.value.stderr,
                },
            )
        )
    return normalize_output(case.id, result.value.stdout)


def _record_all_cases(
    sandbox: SandboxRunner,
    settings: Settings,
    handle: SandboxHandle,
) -> Result[Mapping[str, NormalizedOutput], DepCoverError]:
    outputs: dict[str, NormalizedOutput] = {}
    for case in input_battery():
        recorded = _record_case(sandbox, settings, handle, case)
        if isinstance(recorded, Err):
            return recorded
        outputs[case.id] = recorded.value
    return Ok(MappingProxyType(outputs))


def record_golden(
    sandbox: SandboxRunner,
    settings: Settings,
    snapshot_id: str,
) -> Result[Mapping[str, NormalizedOutput], DepCoverError]:
    acquired = sandbox.acquire(snapshot_id)
    if isinstance(acquired, Err):
        return Err(acquired.error)
    handle = acquired.value
    collected = _record_all_cases(sandbox, settings, handle)
    released = sandbox.release(handle)
    if isinstance(collected, Err):
        return collected
    if isinstance(released, Err):
        return Err(released.error)
    return Ok(collected.value)


__all__ = ("record_golden",)
