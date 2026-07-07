from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Final

from backend.config import Settings
from backend.domain.determinism import Clock, IdGenerator
from backend.domain.enums import SandboxOutcome
from backend.domain.errors import DepCoverError, Err, Ok, Result, SandboxError
from backend.domain.models import (
    CentralityScore,
    GraphLayout,
    LockfileWarning,
    Repo,
    SurgeryPlan,
    UnderwritingReport,
)
from backend.ports.record_store import RecordStore
from backend.ports.sandbox import (
    SandboxCommand,
    SandboxHandle,
    SandboxResult,
    SandboxRunner,
)

_REPORT_ID_PREFIX: Final[str] = "underwriting"

_SANDBOX_WORKDIR: Final[str] = "."
_EMPTY_ENV: Final[dict[str, str]] = {}
_SANDBOX_ENV: Final[Mapping[str, str]] = MappingProxyType(_EMPTY_ENV)

_NODE_MODULES_DIR: Final[str] = "node_modules"
_REMOVAL_ARGV_PREFIX: Final[tuple[str, ...]] = ("rm", "-rf")
_TEST_ARGV: Final[tuple[str, ...]] = ("npm", "test")

_TAP_FAIL_MARKER: Final[str] = "not ok"
_TAP_DESCRIPTION_SEPARATOR: Final[str] = "- "

_TIMEOUT_WARNING_SHAPE: Final[str] = "test_suite_timeout"
_TIMEOUT_WARNING_REASON_TEMPLATE: Final[str] = (
    "kill-test suite exceeded the {timeout_s}s sandbox execution timeout"
)
_ERROR_WARNING_SHAPE: Final[str] = "test_suite_error"
_ERROR_WARNING_REASON: Final[str] = (
    "kill-test suite execution errored inside the sandbox"
)
_UNPARSED_WARNING_SHAPE: Final[str] = "test_output_unrecognized"
_UNPARSED_WARNING_REASON: Final[str] = (
    "kill-test suite reported failure but no failing test names "
    "could be parsed from stdout"
)


def _extract_tap_test_name(stripped_line: str) -> str:
    remainder = stripped_line[len(_TAP_FAIL_MARKER) :].strip()
    tokens = remainder.split(maxsplit=1)
    if tokens and tokens[0].isdigit():
        description = tokens[1] if len(tokens) > 1 else ""
    else:
        description = remainder
    description = description.removeprefix(_TAP_DESCRIPTION_SEPARATOR).strip()
    if description != "":
        return description
    if remainder != "":
        return remainder
    return stripped_line


def _parse_failing_tests(stdout: str) -> tuple[str, ...]:
    failing: list[str] = []
    for line in stdout.splitlines():
        stripped = line.strip()
        if stripped == _TAP_FAIL_MARKER or stripped.startswith(f"{_TAP_FAIL_MARKER} "):
            failing.append(_extract_tap_test_name(stripped))
    return tuple(failing)


def _notes_for_outcome(
    test_result: SandboxResult, failing_tests: tuple[str, ...], timeout_s: float
) -> tuple[LockfileWarning, ...]:
    outcome = test_result.outcome
    if outcome is SandboxOutcome.TIMEOUT:
        return (
            LockfileWarning(
                shape=_TIMEOUT_WARNING_SHAPE,
                reason=_TIMEOUT_WARNING_REASON_TEMPLATE.format(timeout_s=timeout_s),
            ),
        )
    if outcome is SandboxOutcome.ERROR:
        return (LockfileWarning(shape=_ERROR_WARNING_SHAPE, reason=_ERROR_WARNING_REASON),)
    if outcome is SandboxOutcome.FAILED and len(failing_tests) == 0:
        return (
            LockfileWarning(
                shape=_UNPARSED_WARNING_SHAPE, reason=_UNPARSED_WARNING_REASON
            ),
        )
    return ()


class Underwriter:
    def __init__(
        self,
        sandbox: SandboxRunner,
        store: RecordStore,
        settings: Settings,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        self._sandbox = sandbox
        self._store = store
        self._settings = settings
        self._clock = clock
        self._ids = ids

    def run(
        self,
        repo: Repo,
        surgery_plan: SurgeryPlan,
        centrality: Sequence[CentralityScore],
        layout: GraphLayout,
        warnings: Sequence[LockfileWarning],
    ) -> Result[UnderwritingReport, DepCoverError]:
        acquired = self._sandbox.acquire(self._settings.daytona_snapshot_id)
        if isinstance(acquired, Err):
            return Err(acquired.error)
        handle = acquired.value

        kill_test = self._run_kill_test(handle, surgery_plan)
        released = self._sandbox.release(handle)
        if isinstance(kill_test, Err):
            return Err(kill_test.error)
        if isinstance(released, Err):
            return Err(released.error)

        test_result = kill_test.value
        failing_tests = _parse_failing_tests(test_result.stdout)
        notes = _notes_for_outcome(
            test_result, failing_tests, self._settings.sandbox_exec_timeout_s
        )
        report = UnderwritingReport(
            id=self._ids.new_id(_REPORT_ID_PREFIX),
            repo_id=repo.id,
            target_package=surgery_plan.target_package,
            failing_tests=failing_tests,
            affected_file_count=len(surgery_plan.affected_files),
            centrality=tuple(centrality),
            graph_layout=layout,
            warnings=tuple(warnings) + notes,
            created_at=self._clock.now(),
        )
        saved = self._store.save_underwriting(report)
        if isinstance(saved, Err):
            return Err(saved.error)
        return Ok(saved.value)

    def _run_kill_test(
        self, handle: SandboxHandle, surgery_plan: SurgeryPlan
    ) -> Result[SandboxResult, SandboxError]:
        removal = self._sandbox.exec(
            handle,
            self._removal_command(surgery_plan),
            self._settings.sandbox_exec_timeout_s,
        )
        if isinstance(removal, Err):
            return removal
        return self._sandbox.exec(
            handle, self._test_command(), self._settings.sandbox_exec_timeout_s
        )

    def _removal_command(self, surgery_plan: SurgeryPlan) -> SandboxCommand:
        target = f"{_NODE_MODULES_DIR}/{surgery_plan.target_package}"
        return SandboxCommand(
            argv=(*_REMOVAL_ARGV_PREFIX, target),
            cwd=_SANDBOX_WORKDIR,
            env=_SANDBOX_ENV,
        )

    def _test_command(self) -> SandboxCommand:
        return SandboxCommand(argv=_TEST_ARGV, cwd=_SANDBOX_WORKDIR, env=_SANDBOX_ENV)


__all__ = ("Underwriter",)
