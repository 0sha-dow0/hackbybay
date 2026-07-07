"""Tests for Unit 10: AuthProvider port + FakeAuthProvider.

Binds to the coder's actual API:

* ``AuthenticatedUser(id, email)`` is a frozen dataclass.
* ``FakeAuthProvider(tokens: Mapping[str, AuthenticatedUser])`` does an EXACT
  token lookup: no ``"Bearer "`` stripping, no whitespace normalization.
* A blank / whitespace fixture KEY raises ``ValueError`` at construction.
* Error values never echo the token (no secret leak).

Coverage maps to the must-cover cases:

1. Valid token -> ``Ok`` with the same stable user across repeated calls.
2. Empty / whitespace-only tokens -> ``Err(AuthError)``, never anonymous.
3. Unknown token and whitespace-wrapped valid token -> ``Err`` (no normalization).
4. Blank / whitespace fixture key -> ``ValueError`` at construction.
5. ``verify`` is deterministic and side-effect-free; the constructor defensively
   copies the mapping.
6. Errors never contain the token value.
7. Structural conformance: ``FakeAuthProvider`` satisfies ``AuthProvider``.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from backend.adapters.fake.fake_auth import FakeAuthProvider
from backend.domain.errors import AuthError, Err, Ok
from backend.ports.auth import AuthenticatedUser, AuthProvider

_ALICE = AuthenticatedUser(id="u-alice", email="alice@example.com")
_BOB = AuthenticatedUser(id="u-bob", email="bob@example.com")
_SECRET = "sk-live-DO-NOT-LEAK-0f9a8b7c6d5e4f3a2b1c"


def _provider(tokens: dict[str, AuthenticatedUser] | None = None) -> FakeAuthProvider:
    return FakeAuthProvider(tokens if tokens is not None else {"tok-alice": _ALICE})


# --- Structural conformance (also enforced statically by mypy) ---------------


def test_fake_satisfies_protocol() -> None:
    provider: AuthProvider = FakeAuthProvider({"tok-alice": _ALICE})
    result = provider.verify("tok-alice")
    assert isinstance(result, Ok)


def test_authenticated_user_is_frozen() -> None:
    with pytest.raises(FrozenInstanceError):
        setattr(_ALICE, "id", "mutated")


# --- Case 1: valid token -> stable user --------------------------------------


def test_valid_token_returns_ok_user() -> None:
    result = _provider({"tok-alice": _ALICE}).verify("tok-alice")
    assert isinstance(result, Ok)
    assert result.value == _ALICE
    assert result.value.id == "u-alice"
    assert result.value.email == "alice@example.com"


def test_repeated_valid_calls_return_same_stable_user() -> None:
    provider = _provider({"tok-alice": _ALICE})
    results = [provider.verify("tok-alice") for _ in range(1000)]
    assert all(isinstance(r, Ok) for r in results)
    first = results[0]
    assert isinstance(first, Ok)
    for r in results:
        assert isinstance(r, Ok)
        assert r.value == _ALICE
        assert r.value is first.value


def test_distinct_tokens_map_to_distinct_users() -> None:
    provider = _provider({"tok-alice": _ALICE, "tok-bob": _BOB})
    alice = provider.verify("tok-alice")
    bob = provider.verify("tok-bob")
    assert isinstance(alice, Ok)
    assert isinstance(bob, Ok)
    assert alice.value == _ALICE
    assert bob.value == _BOB


# --- Case 2: empty / whitespace tokens -> Err, never anonymous ---------------


@pytest.mark.parametrize("token", ["", "   ", "\t\n", "\t", "\n", " \t \n ", " "])
def test_blank_token_returns_auth_error(token: str) -> None:
    result = _provider({"tok-alice": _ALICE}).verify(token)
    assert isinstance(result, Err)
    assert isinstance(result.error, AuthError)
    assert result.error.code == "auth_error"


def test_blank_token_is_never_a_default_user() -> None:
    provider = _provider({"tok-alice": _ALICE})
    for token in ("", "   ", "\t\n"):
        result = provider.verify(token)
        assert not isinstance(result, Ok)


# --- Case 3: unknown / whitespace-wrapped tokens -> Err (no normalization) ----


def test_unknown_token_returns_auth_error() -> None:
    result = _provider({"tok-alice": _ALICE}).verify("tok-unknown")
    assert isinstance(result, Err)
    assert isinstance(result.error, AuthError)


def test_whitespace_wrapped_valid_token_is_not_normalized() -> None:
    provider = _provider({"tok-alice": _ALICE})
    for wrapped in ("  tok-alice  ", " tok-alice", "tok-alice ", "\ttok-alice\n"):
        result = provider.verify(wrapped)
        assert isinstance(result, Err)
        assert isinstance(result.error, AuthError)


def test_bearer_prefix_is_not_stripped() -> None:
    provider = _provider({"tok-alice": _ALICE})
    result = provider.verify("Bearer tok-alice")
    assert isinstance(result, Err)
    assert isinstance(result.error, AuthError)


def test_lookup_is_case_sensitive_and_exact() -> None:
    provider = _provider({"tok-alice": _ALICE})
    for near_miss in ("TOK-ALICE", "Tok-Alice", "tok-alic", "tok-alicee", "ok-alice"):
        result = provider.verify(near_miss)
        assert isinstance(result, Err)


def test_empty_fixture_rejects_everything() -> None:
    provider = FakeAuthProvider({})
    for token in ("tok-alice", "anything", "  ", ""):
        result = provider.verify(token)
        assert isinstance(result, Err)
        assert isinstance(result.error, AuthError)


# --- Case 4: blank / whitespace fixture key -> ValueError at construction -----


@pytest.mark.parametrize("bad_key", ["", " ", "   ", "\t", "\n", "\t\n", " "])
def test_blank_fixture_key_raises_value_error(bad_key: str) -> None:
    with pytest.raises(ValueError):
        FakeAuthProvider({bad_key: _ALICE})


def test_blank_key_among_valid_keys_still_raises() -> None:
    with pytest.raises(ValueError):
        FakeAuthProvider({"tok-alice": _ALICE, "   ": _BOB})


def test_non_blank_key_with_surrounding_whitespace_is_allowed_and_exact() -> None:
    provider = FakeAuthProvider({" tok-alice ": _ALICE})
    matched = provider.verify(" tok-alice ")
    assert isinstance(matched, Ok)
    assert matched.value == _ALICE
    unmatched = provider.verify("tok-alice")
    assert isinstance(unmatched, Err)


# --- Case 5: determinism + defensive copy ------------------------------------


def test_verify_is_side_effect_free_across_interleaved_calls() -> None:
    provider = _provider({"tok-alice": _ALICE})
    for _ in range(50):
        assert isinstance(provider.verify(""), Err)
        assert isinstance(provider.verify("tok-unknown"), Err)
        ok = provider.verify("tok-alice")
        assert isinstance(ok, Ok)
        assert ok.value == _ALICE


def test_constructor_defensively_copies_added_token() -> None:
    source: dict[str, AuthenticatedUser] = {"tok-alice": _ALICE}
    provider = FakeAuthProvider(source)
    source["tok-bob"] = _BOB
    result = provider.verify("tok-bob")
    assert isinstance(result, Err)
    assert isinstance(result.error, AuthError)


def test_constructor_defensively_copies_removed_token() -> None:
    source: dict[str, AuthenticatedUser] = {"tok-alice": _ALICE}
    provider = FakeAuthProvider(source)
    del source["tok-alice"]
    result = provider.verify("tok-alice")
    assert isinstance(result, Ok)
    assert result.value == _ALICE


def test_constructor_defensively_copies_rebound_token() -> None:
    source: dict[str, AuthenticatedUser] = {"tok-alice": _ALICE}
    provider = FakeAuthProvider(source)
    source["tok-alice"] = _BOB
    result = provider.verify("tok-alice")
    assert isinstance(result, Ok)
    assert result.value == _ALICE


# --- Case 6: no secret leak ---------------------------------------------------


def test_unknown_secret_token_is_not_leaked_in_error() -> None:
    result = _provider({"tok-alice": _ALICE}).verify(_SECRET)
    assert isinstance(result, Err)
    error = result.error
    assert _SECRET not in str(error)
    assert _SECRET not in repr(error)
    assert _SECRET not in error.message
    for key, value in error.context.items():
        assert _SECRET not in key
        assert _SECRET not in value


def test_registered_secret_token_is_not_leaked_when_lookup_misses() -> None:
    provider = FakeAuthProvider({_SECRET: _ALICE})
    result = provider.verify(f"  {_SECRET}  ")
    assert isinstance(result, Err)
    error = result.error
    assert _SECRET not in str(error)
    assert _SECRET not in repr(error)
    assert _SECRET not in error.message


def test_blank_token_error_does_not_echo_input() -> None:
    weird = "\t\n\r\v\f"
    result = _provider({"tok-alice": _ALICE}).verify(weird)
    assert isinstance(result, Err)
    assert weird not in result.error.message
