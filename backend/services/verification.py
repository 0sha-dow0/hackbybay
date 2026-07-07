import difflib
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Final

from backend.config import Settings
from backend.domain.errors import Err, Ok, Result, SandboxError
from backend.domain.models import (
    BehavioralCase,
    BehavioralCaseResult,
    BehavioralDiffResult,
    BuildResult,
    FileContent,
    FileDiff,
    NormalizedOutput,
    RewrittenFile,
    TestResult,
)
from backend.ports.sandbox import (
    SandboxCommand,
    SandboxHandle,
    SandboxResult,
    SandboxRunner,
)
from backend.services.normalizer import normalize_output

_SANDBOX_WORKDIR: Final[str] = "."
_EMPTY_ENV: Final[dict[str, str]] = {}
_SANDBOX_ENV: Final[Mapping[str, str]] = MappingProxyType(_EMPTY_ENV)

_BUILD_ARGV: Final[tuple[str, ...]] = ("npm", "run", "build")
_TEST_ARGV: Final[tuple[str, ...]] = ("npm", "test")
_HARNESS_ARGV_PREFIX: Final[tuple[str, str]] = ("node", "harness.js")

_LOG_STREAM_SEPARATOR: Final[str] = "\n"

_TAP_FAIL_MARKER: Final[str] = "not ok"
_TAP_DESCRIPTION_SEPARATOR: Final[str] = "- "

_DIFF_CONTEXT_LINES: Final[int] = 3
_DIFF_LINE_TERMINATOR: Final[str] = ""
_DIFF_JOIN: Final[str] = "\n"

_MISSING_GOLDEN_MESSAGE: Final[str] = (
    "behavioral diff requires a pre-recorded golden output for every battery case"
)
_NORMALIZE_FAILURE_MESSAGE: Final[str] = (
    "behavioral diff could not normalize the patched sandbox output for a case"
)


def _files_map(files: Sequence[RewrittenFile]) -> Mapping[str, str]:
    return {file.path: file.text for file in files}


def _combined_log(result: SandboxResult) -> str:
    return _LOG_STREAM_SEPARATOR.join((result.stdout, result.stderr))


def _build_command() -> SandboxCommand:
    return SandboxCommand(argv=_BUILD_ARGV, cwd=_SANDBOX_WORKDIR, env=_SANDBOX_ENV)


def _test_command() -> SandboxCommand:
    return SandboxCommand(argv=_TEST_ARGV, cwd=_SANDBOX_WORKDIR, env=_SANDBOX_ENV)


def _harness_command(case: BehavioralCase) -> SandboxCommand:
    return SandboxCommand(
        argv=(*_HARNESS_ARGV_PREFIX, case.id),
        cwd=_SANDBOX_WORKDIR,
        env=_SANDBOX_ENV,
    )


def _is_tap_failure(stripped_line: str) -> bool:
    return stripped_line == _TAP_FAIL_MARKER or stripped_line.startswith(
        f"{_TAP_FAIL_MARKER} "
    )


def _tap_failure_name(stripped_line: str) -> str:
    remainder = stripped_line[len(_TAP_FAIL_MARKER) :].strip()
    tokens = remainder.split(maxsplit=1)
    if tokens and tokens[0].isdigit():
        described = tokens[1] if len(tokens) > 1 else ""
    else:
        described = remainder
    described = described.removeprefix(_TAP_DESCRIPTION_SEPARATOR).strip()
    if described != "":
        return described
    if remainder != "":
        return remainder
    return stripped_line


def _parse_failing_tests(stdout: str) -> tuple[str, ...]:
    failing: list[str] = []
    for line in stdout.splitlines():
        stripped = line.strip()
        if _is_tap_failure(stripped):
            failing.append(_tap_failure_name(stripped))
    return tuple(failing)


def _unified_diff(path: str, before: str, after: str) -> str:
    lines = difflib.unified_diff(
        before.splitlines(),
        after.splitlines(),
        fromfile=path,
        tofile=path,
        n=_DIFF_CONTEXT_LINES,
        lineterm=_DIFF_LINE_TERMINATOR,
    )
    return _DIFF_JOIN.join(lines)


class VerificationEngine:
    def __init__(self, sandbox: SandboxRunner, settings: Settings) -> None:
        self._sandbox = sandbox
        self._settings = settings

    def build_check(
        self, files: Sequence[RewrittenFile], snapshot_id: str
    ) -> Result[BuildResult, SandboxError]:
        executed = self._run_single(files, _build_command(), snapshot_id)
        if isinstance(executed, Err):
            return Err(executed.error)
        result = executed.value
        return Ok(BuildResult(outcome=result.outcome, log=_combined_log(result)))

    def test_suite(
        self, files: Sequence[RewrittenFile], snapshot_id: str
    ) -> Result[TestResult, SandboxError]:
        executed = self._run_single(files, _test_command(), snapshot_id)
        if isinstance(executed, Err):
            return Err(executed.error)
        result = executed.value
        return Ok(
            TestResult(
                outcome=result.outcome,
                failing_tests=_parse_failing_tests(result.stdout),
                log=_combined_log(result),
            )
        )

    def behavioral_diff(
        self,
        patched_files: Sequence[RewrittenFile],
        golden: Mapping[str, NormalizedOutput],
        battery: Sequence[BehavioralCase],
        snapshot_id: str,
    ) -> Result[BehavioralDiffResult, SandboxError]:
        acquired = self._sandbox.acquire(snapshot_id)
        if isinstance(acquired, Err):
            return Err(acquired.error)
        handle = acquired.value
        cases = self._run_battery(handle, patched_files, golden, battery)
        released = self._sandbox.release(handle)
        if isinstance(cases, Err):
            return Err(cases.error)
        if isinstance(released, Err):
            return Err(released.error)
        results = cases.value
        matched = all(result.equal for result in results)
        return Ok(BehavioralDiffResult(matched=matched, per_case=results))

    def diff_files(
        self, before: Sequence[FileContent], after: Sequence[RewrittenFile]
    ) -> tuple[FileDiff, ...]:
        before_by_path = {file.path: file.text for file in before}
        after_by_path = {file.path: file.text for file in after}
        diffs: list[FileDiff] = []
        for path in sorted(before_by_path.keys() | after_by_path.keys()):
            before_text = before_by_path.get(path, "")
            after_text = after_by_path.get(path, "")
            diffs.append(
                FileDiff(
                    path=path,
                    unified_diff=_unified_diff(path, before_text, after_text),
                    before=before_text,
                    after=after_text,
                )
            )
        return tuple(diffs)

    def _run_single(
        self,
        files: Sequence[RewrittenFile],
        command: SandboxCommand,
        snapshot_id: str,
    ) -> Result[SandboxResult, SandboxError]:
        acquired = self._sandbox.acquire(snapshot_id)
        if isinstance(acquired, Err):
            return Err(acquired.error)
        handle = acquired.value
        executed = self._prepare_and_exec(handle, files, command)
        released = self._sandbox.release(handle)
        if isinstance(executed, Err):
            return Err(executed.error)
        if isinstance(released, Err):
            return Err(released.error)
        return Ok(executed.value)

    def _prepare_and_exec(
        self,
        handle: SandboxHandle,
        files: Sequence[RewrittenFile],
        command: SandboxCommand,
    ) -> Result[SandboxResult, SandboxError]:
        written = self._sandbox.write_files(handle, _files_map(files))
        if isinstance(written, Err):
            return Err(written.error)
        return self._sandbox.exec(
            handle, command, self._settings.sandbox_exec_timeout_s
        )

    def _run_battery(
        self,
        handle: SandboxHandle,
        patched_files: Sequence[RewrittenFile],
        golden: Mapping[str, NormalizedOutput],
        battery: Sequence[BehavioralCase],
    ) -> Result[tuple[BehavioralCaseResult, ...], SandboxError]:
        written = self._sandbox.write_files(handle, _files_map(patched_files))
        if isinstance(written, Err):
            return Err(written.error)
        results: list[BehavioralCaseResult] = []
        for case in battery:
            case_result = self._run_case(handle, case, golden)
            if isinstance(case_result, Err):
                return Err(case_result.error)
            results.append(case_result.value)
        return Ok(tuple(results))

    def _run_case(
        self,
        handle: SandboxHandle,
        case: BehavioralCase,
        golden: Mapping[str, NormalizedOutput],
    ) -> Result[BehavioralCaseResult, SandboxError]:
        golden_output = golden.get(case.id)
        if golden_output is None:
            return Err(SandboxError(_MISSING_GOLDEN_MESSAGE, {"case_id": case.id}))
        executed = self._sandbox.exec(
            handle, _harness_command(case), self._settings.sandbox_exec_timeout_s
        )
        if isinstance(executed, Err):
            return Err(executed.error)
        normalized = normalize_output(case.id, executed.value.stdout)
        if isinstance(normalized, Err):
            return Err(
                SandboxError(
                    _NORMALIZE_FAILURE_MESSAGE,
                    {"case_id": case.id, "cause": normalized.error.message},
                )
            )
        candidate = normalized.value
        return Ok(
            BehavioralCaseResult(
                case_id=case.id,
                golden=golden_output,
                candidate=candidate,
                equal=candidate.normalized == golden_output.normalized,
            )
        )


__all__ = ("VerificationEngine",)
