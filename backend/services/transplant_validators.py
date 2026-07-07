import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, assert_never

from backend.config import Settings
from backend.domain.constants import TARGET_PACKAGE
from backend.domain.enums import SandboxOutcome
from backend.domain.errors import (
    DepCoverError,
    Err,
    Ok,
    Result,
    SandboxError,
    SandboxTimeoutError,
    ValidationRejectedError,
)
from backend.domain.models import (
    CallSite,
    FileContent,
    RewrittenFile,
    SurgeryPlan,
    TransplantOutput,
)
from backend.ports.sandbox import (
    SandboxCommand,
    SandboxHandle,
    SandboxResult,
    SandboxRunner,
)
from backend.services.call_site_scanner import scan_call_sites

_FENCE_OPEN_RE: Final = re.compile(r"^`{3,}[ \t]*[A-Za-z0-9_.+-]*[ \t]*$")
_FENCE_CLOSE_RE: Final = re.compile(r"^`{3,}[ \t]*$")

_LINE_SEPARATOR: Final[str] = "\n"
_EMPTY_LINE: Final[str] = ""

_NODE_CHECK_ARGV_PREFIX: Final[tuple[str, str]] = ("node", "--check")
_SANDBOX_WORKDIR: Final[str] = "."
_EMPTY_ENV: Final[dict[str, str]] = {}
_SANDBOX_ENV: Final[Mapping[str, str]] = MappingProxyType(_EMPTY_ENV)

_SURVIVOR_SEPARATOR: Final[str] = ", "

_NODE_CHECK_REJECT_MESSAGE: Final[str] = (
    "rewritten file failed node --check syntax validation"
)
_NODE_CHECK_TIMEOUT_MESSAGE: Final[str] = (
    "node --check syntax validation timed out"
)
_NODE_CHECK_ERROR_MESSAGE: Final[str] = (
    "node --check syntax validation failed inside the sandbox"
)
_AXIOS_SURVIVOR_MESSAGE: Final[str] = (
    "rewritten output still references the target package after transplant"
)


@dataclass(frozen=True)
class ValidationReport:
    fences_stripped: bool
    node_check_ok: bool
    axios_survivors: tuple[CallSite, ...]
    ok: bool


def _first_content_index(lines: Sequence[str]) -> int | None:
    for index, line in enumerate(lines):
        if line.strip() != _EMPTY_LINE:
            return index
    return None


def _last_content_index(lines: Sequence[str], after: int) -> int | None:
    for offset in range(len(lines) - 1, after, -1):
        if lines[offset].strip() != _EMPTY_LINE:
            return offset
    return None


def strip_markdown_fences(text: str) -> str:
    lines = text.split(_LINE_SEPARATOR)
    open_index = _first_content_index(lines)
    if open_index is None:
        return text
    if _FENCE_OPEN_RE.match(lines[open_index].strip()) is None:
        return text
    close_index = _last_content_index(lines, open_index)
    if close_index is None:
        return text
    if _FENCE_CLOSE_RE.match(lines[close_index].strip()) is None:
        return text
    return _LINE_SEPARATOR.join(lines[open_index + 1 : close_index])


def _files_map(files: Sequence[RewrittenFile]) -> Mapping[str, str]:
    return {file.path: file.text for file in files}


def _node_check_command(path: str) -> SandboxCommand:
    return SandboxCommand(
        argv=(*_NODE_CHECK_ARGV_PREFIX, path),
        cwd=_SANDBOX_WORKDIR,
        env=_SANDBOX_ENV,
    )


def _clean_output(output: TransplantOutput) -> tuple[TransplantOutput, bool]:
    cleaned_files: list[RewrittenFile] = []
    fences_stripped = False
    for file in output.files:
        cleaned_text = strip_markdown_fences(file.text)
        if cleaned_text != file.text:
            fences_stripped = True
        cleaned_files.append(RewrittenFile(path=file.path, text=cleaned_text))
    cleaned = TransplantOutput(
        attempt=output.attempt,
        files=tuple(cleaned_files),
        raw_model_text=output.raw_model_text,
    )
    return cleaned, fences_stripped


class TransplantValidator:
    def __init__(self, sandbox: SandboxRunner, settings: Settings) -> None:
        self._sandbox = sandbox
        self._settings = settings

    def validate(
        self, output: TransplantOutput, plan: SurgeryPlan, snapshot_id: str
    ) -> Result[tuple[TransplantOutput, ValidationReport], DepCoverError]:
        cleaned, fences_stripped = _clean_output(output)
        node_checked = self._node_check(cleaned, snapshot_id)
        if isinstance(node_checked, Err):
            return Err(node_checked.error)
        scanned = self._axios_survivors(cleaned)
        if isinstance(scanned, Err):
            return Err(scanned.error)
        survivors = scanned.value
        if survivors:
            return Err(
                ValidationRejectedError(
                    _AXIOS_SURVIVOR_MESSAGE,
                    {
                        "target_package": TARGET_PACKAGE,
                        "survivor_count": str(len(survivors)),
                        "survivor_symbols": _SURVIVOR_SEPARATOR.join(
                            site.symbol for site in survivors
                        ),
                    },
                )
            )
        report = ValidationReport(
            fences_stripped=fences_stripped,
            node_check_ok=True,
            axios_survivors=(),
            ok=True,
        )
        return Ok((cleaned, report))

    def _node_check(
        self, cleaned: TransplantOutput, snapshot_id: str
    ) -> Result[None, DepCoverError]:
        acquired = self._sandbox.acquire(snapshot_id)
        if isinstance(acquired, Err):
            return Err(acquired.error)
        handle = acquired.value
        checked = self._write_and_check(handle, cleaned)
        released = self._sandbox.release(handle)
        if isinstance(checked, Err):
            return Err(checked.error)
        if isinstance(released, Err):
            return Err(released.error)
        return Ok(None)

    def _write_and_check(
        self, handle: SandboxHandle, cleaned: TransplantOutput
    ) -> Result[None, DepCoverError]:
        written = self._sandbox.write_files(handle, _files_map(cleaned.files))
        if isinstance(written, Err):
            return Err(written.error)
        for file in cleaned.files:
            checked = self._check_file(handle, file.path)
            if isinstance(checked, Err):
                return Err(checked.error)
        return Ok(None)

    def _check_file(
        self, handle: SandboxHandle, path: str
    ) -> Result[None, DepCoverError]:
        executed = self._sandbox.exec(
            handle, _node_check_command(path), self._settings.sandbox_exec_timeout_s
        )
        if isinstance(executed, Err):
            return Err(executed.error)
        return self._interpret_outcome(executed.value, path)

    def _interpret_outcome(
        self, result: SandboxResult, path: str
    ) -> Result[None, DepCoverError]:
        outcome = result.outcome
        if outcome is SandboxOutcome.PASSED:
            return Ok(None)
        if outcome is SandboxOutcome.FAILED:
            return Err(
                ValidationRejectedError(
                    _NODE_CHECK_REJECT_MESSAGE,
                    {"path": path, "stderr": result.stderr},
                )
            )
        if outcome is SandboxOutcome.TIMEOUT:
            return Err(
                SandboxTimeoutError(
                    _NODE_CHECK_TIMEOUT_MESSAGE,
                    {
                        "path": path,
                        "timeout_s": str(self._settings.sandbox_exec_timeout_s),
                    },
                )
            )
        if outcome is SandboxOutcome.ERROR:
            return Err(
                SandboxError(
                    _NODE_CHECK_ERROR_MESSAGE,
                    {"path": path, "stderr": result.stderr},
                )
            )
        assert_never(outcome)

    def _axios_survivors(
        self, cleaned: TransplantOutput
    ) -> Result[tuple[CallSite, ...], DepCoverError]:
        contents = tuple(
            FileContent(path=file.path, text=file.text) for file in cleaned.files
        )
        scanned = scan_call_sites(contents, TARGET_PACKAGE)
        if isinstance(scanned, Err):
            return Err(scanned.error)
        return Ok(scanned.value)


__all__ = (
    "TransplantValidator",
    "ValidationReport",
    "strip_markdown_fences",
)
