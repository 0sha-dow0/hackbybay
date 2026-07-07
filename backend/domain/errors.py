from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import ClassVar, Final, Generic, TypeAlias, TypeGuard, TypeVar


class DepCoverError(Exception):
    code: ClassVar[str] = "depcover_error"

    def __init__(self, message: str, context: Mapping[str, str] | None = None) -> None:
        super().__init__(message)
        self.message: str = message
        self.context: Mapping[str, str] = MappingProxyType(
            dict(context) if context is not None else {}
        )

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


class IngestError(DepCoverError):
    code: ClassVar[str] = "ingest_error"


class LockfileParseError(IngestError):
    code: ClassVar[str] = "lockfile_parse_error"


class GraphError(DepCoverError):
    code: ClassVar[str] = "graph_error"


class SandboxError(DepCoverError):
    code: ClassVar[str] = "sandbox_error"


class SandboxTimeoutError(SandboxError):
    code: ClassVar[str] = "sandbox_timeout_error"


class SandboxUnavailableError(SandboxError):
    code: ClassVar[str] = "sandbox_unavailable_error"


class LlmError(DepCoverError):
    code: ClassVar[str] = "llm_error"


class LlmTimeoutError(LlmError):
    code: ClassVar[str] = "llm_timeout_error"


class LlmMalformedOutputError(LlmError):
    code: ClassVar[str] = "llm_malformed_output_error"


class ValidationRejectedError(DepCoverError):
    code: ClassVar[str] = "validation_rejected_error"


class RecordStoreError(DepCoverError):
    code: ClassVar[str] = "record_store_error"


class AuthError(DepCoverError):
    code: ClassVar[str] = "auth_error"


class GitHubError(DepCoverError):
    code: ClassVar[str] = "github_error"


class RateLimitError(GitHubError):
    code: ClassVar[str] = "rate_limit_error"


class StateTransitionError(DepCoverError):
    code: ClassVar[str] = "state_transition_error"


class ConfigError(DepCoverError):
    code: ClassVar[str] = "config_error"


def _collect_error_classes(root: type[DepCoverError]) -> list[type[DepCoverError]]:
    collected: list[type[DepCoverError]] = [root]
    for subclass in root.__subclasses__():
        collected.extend(_collect_error_classes(subclass))
    return collected


_ERROR_CLASSES: Final[tuple[type[DepCoverError], ...]] = tuple(
    _collect_error_classes(DepCoverError)
)
_ERROR_CODES: Final[tuple[str, ...]] = tuple(
    error_class.code for error_class in _ERROR_CLASSES
)

assert all(code != "" for code in _ERROR_CODES)
assert len(_ERROR_CODES) == len(set(_ERROR_CODES))

ERROR_REGISTRY: Final[Mapping[str, type[DepCoverError]]] = MappingProxyType(
    {error_class.code: error_class for error_class in _ERROR_CLASSES}
)


T = TypeVar("T")
E = TypeVar("E", bound=DepCoverError)


@dataclass(frozen=True)
class Ok(Generic[T]):
    value: T


@dataclass(frozen=True)
class Err(Generic[E]):
    error: E


Result: TypeAlias = Ok[T] | Err[E]


def is_ok(result: Result[T, E]) -> TypeGuard[Ok[T]]:
    return isinstance(result, Ok)


def is_err(result: Result[T, E]) -> TypeGuard[Err[E]]:
    return isinstance(result, Err)
