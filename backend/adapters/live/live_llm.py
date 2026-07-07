from __future__ import annotations

import json
import os
from typing import Final, Literal

import httpx

from backend.config import Settings
from backend.domain.enums import LlmRole
from backend.domain.errors import (
    ConfigError,
    Err,
    LlmError,
    LlmMalformedOutputError,
    LlmTimeoutError,
    Ok,
    Result,
)
from backend.ports.llm import LlmClient, LlmClientFactory, LlmRequest, LlmResponse

_CHAT_COMPLETIONS_PATH: Final[str] = "/chat/completions"
_HEADER_AUTHORIZATION: Final[str] = "Authorization"
_HEADER_CONTENT_TYPE: Final[str] = "Content-Type"
_HEADER_REFERER: Final[str] = "HTTP-Referer"
_HEADER_TITLE: Final[str] = "X-Title"
_BEARER_PREFIX: Final[str] = "Bearer "
_CONTENT_TYPE_JSON: Final[str] = "application/json"
_APP_REFERER: Final[str] = "https://github.com/depcover/depcover"
_APP_TITLE: Final[str] = "DepCover"
_MAX_ERROR_BODY_CHARS: Final[int] = 300
_FINISH_REASON_LENGTH: Final[str] = "length"
_MALFORMED_MESSAGE: Final[str] = "unparseable completion"


def _map_finish_reason(value: object) -> Literal["stop", "length"]:
    if value == _FINISH_REASON_LENGTH:
        return "length"
    return "stop"


def _malformed(reason: str) -> Result[LlmResponse, LlmError]:
    return Err(LlmMalformedOutputError(_MALFORMED_MESSAGE, {"reason": reason}))


def _parse_completion(
    parsed: object, fallback_model: str
) -> Result[LlmResponse, LlmError]:
    if not isinstance(parsed, dict):
        return _malformed("response body is not a json object")
    choices = parsed.get("choices")
    if not isinstance(choices, list) or len(choices) == 0:
        return _malformed("response contains no choices")
    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        return _malformed("first choice is not an object")
    message = first_choice.get("message")
    if not isinstance(message, dict):
        return _malformed("first choice has no message object")
    content = message.get("content")
    if not isinstance(content, str):
        return _malformed("message content is missing or not a string")
    finish_reason = _map_finish_reason(first_choice.get("finish_reason"))
    model_value = parsed.get("model")
    model = model_value if isinstance(model_value, str) else fallback_model
    return Ok(LlmResponse(text=content, model=model, finish_reason=finish_reason))


class LiveLlmClient(LlmClient):
    def __init__(
        self, base_url: str, model: str, api_key: str, timeout_s: float
    ) -> None:
        self._base_url = base_url
        self._model = model
        self._api_key = api_key
        self._timeout_s = timeout_s

    def complete(self, req: LlmRequest) -> Result[LlmResponse, LlmError]:
        url = f"{self._base_url.rstrip('/')}{_CHAT_COMPLETIONS_PATH}"
        headers = {
            _HEADER_AUTHORIZATION: f"{_BEARER_PREFIX}{self._api_key}",
            _HEADER_CONTENT_TYPE: _CONTENT_TYPE_JSON,
            _HEADER_REFERER: _APP_REFERER,
            _HEADER_TITLE: _APP_TITLE,
        }
        body: dict[str, object] = {
            "model": self._model,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in req.messages
            ],
            "temperature": req.temperature,
            "max_tokens": req.max_tokens,
        }
        try:
            with httpx.Client(timeout=self._timeout_s) as client:
                response = client.post(url, headers=headers, json=body)
        except httpx.TimeoutException as error:
            return Err(LlmTimeoutError("llm request timed out", {"error": str(error)}))
        except httpx.HTTPError as error:
            return Err(LlmError("llm transport failure", {"error": str(error)}))
        if not response.is_success:
            return Err(
                LlmError(
                    "upstream status",
                    {
                        "status": str(response.status_code),
                        "body": response.text[:_MAX_ERROR_BODY_CHARS],
                    },
                )
            )
        try:
            parsed: object = response.json()
        except json.JSONDecodeError as error:
            return Err(
                LlmMalformedOutputError(_MALFORMED_MESSAGE, {"reason": str(error)})
            )
        return _parse_completion(parsed, self._model)


class LiveLlmClientFactory(LlmClientFactory):
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def for_role(self, role: LlmRole) -> Result[LlmClient, ConfigError]:
        config = self._settings.role(role)
        api_key = os.environ.get(config.api_key_env)
        if api_key is None or api_key.strip() == "":
            return Err(
                ConfigError(
                    "missing api key env",
                    {"role": role.value, "env": config.api_key_env},
                )
            )
        return Ok(
            LiveLlmClient(
                config.base_url, config.model, api_key, config.timeout_s
            )
        )


__all__ = (
    "LiveLlmClient",
    "LiveLlmClientFactory",
)
