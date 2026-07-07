from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from backend.domain.errors import AuthError, Err, Ok, Result
from backend.ports.auth import AuthenticatedUser

_EMPTY_TOKEN_MESSAGE: Final = "bearer token is empty or whitespace"
_UNKNOWN_TOKEN_MESSAGE: Final = "bearer token is not recognized"
_BLANK_FIXTURE_KEY_MESSAGE: Final = "token fixture contains an empty or whitespace key"


class FakeAuthProvider:
    def __init__(self, tokens: Mapping[str, AuthenticatedUser]) -> None:
        for token in tokens:
            if not token.strip():
                raise ValueError(_BLANK_FIXTURE_KEY_MESSAGE)
        self._tokens: Mapping[str, AuthenticatedUser] = MappingProxyType(dict(tokens))

    def verify(self, bearer_token: str) -> Result[AuthenticatedUser, AuthError]:
        if not bearer_token.strip():
            return Err(AuthError(_EMPTY_TOKEN_MESSAGE))
        user = self._tokens.get(bearer_token)
        if user is None:
            return Err(AuthError(_UNKNOWN_TOKEN_MESSAGE))
        return Ok(user)
