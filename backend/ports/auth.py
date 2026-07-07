from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from backend.domain.errors import AuthError, Result


@dataclass(frozen=True)
class AuthenticatedUser:
    id: str
    email: str


class AuthProvider(Protocol):
    def verify(self, bearer_token: str) -> Result[AuthenticatedUser, AuthError]: ...
