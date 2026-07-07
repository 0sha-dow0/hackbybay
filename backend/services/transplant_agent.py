from typing import Final

from backend.domain.enums import LlmRole
from backend.domain.errors import (
    DepCoverError,
    Err,
    LlmMalformedOutputError,
    Ok,
    Result,
)
from backend.domain.models import (
    RewrittenFile,
    TransplantOutput,
    TransplantRequest,
)
from backend.ports.llm import LlmClientFactory, LlmRequest
from backend.services.transplant_prompt import build_transplant_messages

_TRANSPLANT_TEMPERATURE: Final[float] = 0.0
_TRANSPLANT_MAX_TOKENS: Final[int] = 8192
_TRUNCATED_FINISH_REASON: Final = "length"

_NEWLINE: Final[str] = "\n"
_OPEN_PREFIX: Final[str] = '<rewritten_file path="'
_OPEN_SUFFIX: Final[str] = '">'
_CLOSE_MARKER: Final[str] = "</rewritten_file>"
_OPEN_MIN_LENGTH: Final[int] = len(_OPEN_PREFIX) + len(_OPEN_SUFFIX)

_PATH_SEPARATOR: Final[str] = ", "
_EMPTY_PLACEHOLDER: Final[str] = "none"

_CTX_FINISH_REASON: Final[str] = "finish_reason"
_CTX_PATH: Final[str] = "path"
_CTX_DUPLICATED: Final[str] = "duplicated"
_CTX_MISSING: Final[str] = "missing"
_CTX_EXTRA: Final[str] = "extra"

_TRUNCATED_MESSAGE: Final[str] = (
    "transplant response was truncated before completion"
)
_UNCLOSED_MESSAGE: Final[str] = (
    "transplant response opened a rewritten_file block that was never closed"
)
_DUPLICATE_MESSAGE: Final[str] = (
    "transplant response emitted the same file path more than once"
)
_MISMATCH_MESSAGE: Final[str] = (
    "transplant response file paths do not match the requested file set"
)


def _open_marker_path(line: str) -> str | None:
    if not line.startswith(_OPEN_PREFIX):
        return None
    if not line.endswith(_OPEN_SUFFIX):
        return None
    if len(line) < _OPEN_MIN_LENGTH:
        return None
    return line[len(_OPEN_PREFIX) : -len(_OPEN_SUFFIX)]


def _format_paths(paths: list[str]) -> str:
    return _PATH_SEPARATOR.join(paths) if paths else _EMPTY_PLACEHOLDER


def _by_path(file: RewrittenFile) -> str:
    return file.path


def _parse_rewritten_files(
    text: str,
) -> Result[tuple[RewrittenFile, ...], LlmMalformedOutputError]:
    lines = text.split(_NEWLINE)
    total = len(lines)
    collected: list[RewrittenFile] = []
    index = 0
    while index < total:
        path = _open_marker_path(lines[index])
        if path is None:
            index += 1
            continue
        index += 1
        body_start = index
        while index < total and lines[index] != _CLOSE_MARKER:
            index += 1
        if index >= total:
            return Err(
                LlmMalformedOutputError(_UNCLOSED_MESSAGE, {_CTX_PATH: path})
            )
        body = _NEWLINE.join(lines[body_start:index])
        collected.append(RewrittenFile(path=path, text=body))
        index += 1
    return Ok(tuple(collected))


def _validate_paths(
    files: tuple[RewrittenFile, ...], request: TransplantRequest
) -> Result[None, LlmMalformedOutputError]:
    emitted = [file.path for file in files]
    present = set(emitted)
    if len(present) != len(emitted):
        duplicated = sorted(path for path in present if emitted.count(path) > 1)
        return Err(
            LlmMalformedOutputError(
                _DUPLICATE_MESSAGE,
                {_CTX_DUPLICATED: _format_paths(duplicated)},
            )
        )
    expected = {file.path for file in request.files}
    if present != expected:
        return Err(
            LlmMalformedOutputError(
                _MISMATCH_MESSAGE,
                {
                    _CTX_MISSING: _format_paths(sorted(expected - present)),
                    _CTX_EXTRA: _format_paths(sorted(present - expected)),
                },
            )
        )
    return Ok(None)


class TransplantAgent:
    def __init__(self, llm: LlmClientFactory) -> None:
        self._llm: LlmClientFactory = llm

    def run(
        self, request: TransplantRequest, attempt: int
    ) -> Result[TransplantOutput, DepCoverError]:
        messages_result = build_transplant_messages(request)
        if isinstance(messages_result, Err):
            return messages_result
        client_result = self._llm.for_role(LlmRole.TRANSPLANT)
        if isinstance(client_result, Err):
            return Err(client_result.error)
        payload = LlmRequest(
            role=LlmRole.TRANSPLANT,
            messages=messages_result.value,
            temperature=_TRANSPLANT_TEMPERATURE,
            max_tokens=_TRANSPLANT_MAX_TOKENS,
        )
        completion = client_result.value.complete(payload)
        if isinstance(completion, Err):
            return Err(completion.error)
        response = completion.value
        if response.finish_reason == _TRUNCATED_FINISH_REASON:
            return Err(
                LlmMalformedOutputError(
                    _TRUNCATED_MESSAGE,
                    {_CTX_FINISH_REASON: response.finish_reason},
                )
            )
        parse_result = _parse_rewritten_files(response.text)
        if isinstance(parse_result, Err):
            return Err(parse_result.error)
        validation = _validate_paths(parse_result.value, request)
        if isinstance(validation, Err):
            return Err(validation.error)
        ordered = tuple(sorted(parse_result.value, key=_by_path))
        return Ok(
            TransplantOutput(
                attempt=attempt,
                files=ordered,
                raw_model_text=response.text,
            )
        )


__all__ = ("TransplantAgent",)
