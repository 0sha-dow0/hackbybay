from __future__ import annotations

from collections.abc import Mapping, Sequence

from backend.domain.enums import LlmRole
from backend.domain.errors import ConfigError, Err, LlmError, Ok, Result
from backend.ports.llm import LlmClient, LlmClientFactory, LlmRequest, LlmResponse


class FakeLlmClient(LlmClient):
    def __init__(self, role: LlmRole, responses: Sequence[LlmResponse]) -> None:
        self._role = role
        self._responses: tuple[LlmResponse, ...] = tuple(responses)
        self._cursor = 0

    def complete(self, req: LlmRequest) -> Result[LlmResponse, LlmError]:
        if self._cursor >= len(self._responses):
            return Err(
                LlmError(
                    "fake llm script is exhausted for role",
                    {"role": self._role.value},
                )
            )
        response = self._responses[self._cursor]
        self._cursor += 1
        return Ok(response)


class FakeLlmClientFactory(LlmClientFactory):
    def __init__(self, scripted: Mapping[LlmRole, Sequence[LlmResponse]]) -> None:
        self._clients: dict[LlmRole, LlmClient] = {
            role: FakeLlmClient(role, responses)
            for role, responses in scripted.items()
        }

    def for_role(self, role: LlmRole) -> Result[LlmClient, ConfigError]:
        client = self._clients.get(role)
        if client is None:
            return Err(
                ConfigError(
                    "no fake llm script is configured for role",
                    {"role": role.value},
                )
            )
        return Ok(client)


__all__ = (
    "FakeLlmClient",
    "FakeLlmClientFactory",
)
