from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from backend.domain.enums import LlmRole
from backend.domain.errors import ConfigError, LlmError, Result


@dataclass(frozen=True)
class LlmMessage:
    role: Literal["system", "user"]
    content: str


@dataclass(frozen=True)
class LlmRequest:
    role: LlmRole
    messages: tuple[LlmMessage, ...]
    temperature: float
    max_tokens: int


@dataclass(frozen=True)
class LlmResponse:
    text: str
    model: str
    finish_reason: Literal["stop", "length"]


class LlmClient(Protocol):
    def complete(self, req: LlmRequest) -> Result[LlmResponse, LlmError]: ...


class LlmClientFactory(Protocol):
    def for_role(self, role: LlmRole) -> Result[LlmClient, ConfigError]: ...


__all__ = (
    "LlmClient",
    "LlmClientFactory",
    "LlmMessage",
    "LlmRequest",
    "LlmResponse",
)
