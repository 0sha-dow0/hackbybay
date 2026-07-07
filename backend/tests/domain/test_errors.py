"""Tests for backend.domain.errors.

Verifies the typed error hierarchy and the Result algebra:

* every concrete error class is instantiable with and without ``context`` and
  renders as ``"[<code>] <message>"`` with a non-empty ``code``;
* ``ERROR_REGISTRY`` maps each stable ``code`` to the correct class, codes are
  pairwise distinct, and the registry is a read-only mapping;
* the inheritance lattice holds and ``except`` on a base catches subclasses;
* ``context`` is an immutable, defensively-copied mapping;
* ``Ok``/``Err`` are frozen, value-equal, discriminate via ``isinstance`` and
  structural ``match``, and ``is_ok``/``is_err`` behave correctly at runtime;
* ``is_ok``/``is_err`` narrow statically (proved by the scoped mypy run through
  the ``_static_narrowing`` helper below).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from importlib import reload
from types import MappingProxyType
from typing import assert_type

import pytest

import backend.domain.errors as errors_module
from backend.domain.errors import (
    AuthError,
    ConfigError,
    DepCoverError,
    ERROR_REGISTRY,
    Err,
    GitHubError,
    GraphError,
    IngestError,
    LlmError,
    LlmMalformedOutputError,
    LlmTimeoutError,
    LockfileParseError,
    Ok,
    RateLimitError,
    RecordStoreError,
    Result,
    SandboxError,
    SandboxTimeoutError,
    SandboxUnavailableError,
    StateTransitionError,
    ValidationRejectedError,
    is_err,
    is_ok,
)

# --- Contract table --------------------------------------------------------

# The complete, published class -> stable code mapping. ``DepCoverError`` (the
# base) is itself instantiable and is collected into the registry, so it is
# part of the contract. Taken verbatim from the module contract; this guards
# both wrong codes and a wrong/missing class in the registry.
EXPECTED_ERROR_CODES: dict[type[DepCoverError], str] = {
    DepCoverError: "depcover_error",
    IngestError: "ingest_error",
    LockfileParseError: "lockfile_parse_error",
    GraphError: "graph_error",
    SandboxError: "sandbox_error",
    SandboxTimeoutError: "sandbox_timeout_error",
    SandboxUnavailableError: "sandbox_unavailable_error",
    LlmError: "llm_error",
    LlmTimeoutError: "llm_timeout_error",
    LlmMalformedOutputError: "llm_malformed_output_error",
    ValidationRejectedError: "validation_rejected_error",
    RecordStoreError: "record_store_error",
    AuthError: "auth_error",
    GitHubError: "github_error",
    RateLimitError: "rate_limit_error",
    StateTransitionError: "state_transition_error",
    ConfigError: "config_error",
}

ALL_ERROR_CLASSES: tuple[type[DepCoverError], ...] = tuple(EXPECTED_ERROR_CODES.keys())


# --- Acceptance criterion 1: instantiation + string rendering --------------


def test_instantiable_with_context_and_message_only() -> None:
    for cls, code in EXPECTED_ERROR_CODES.items():
        with_ctx = cls("boom", {"pkg": "left-pad"})
        assert with_ctx.message == "boom", f"{cls.__name__} lost its message"
        assert with_ctx.context == {"pkg": "left-pad"}, (
            f"{cls.__name__} lost its context"
        )

        message_only = cls("boom")
        assert message_only.message == "boom"
        assert message_only.context == {}, (
            f"{cls.__name__} default context is not empty: {message_only.context!r}"
        )


def test_str_contains_code_and_message_and_code_non_empty() -> None:
    for cls, code in EXPECTED_ERROR_CODES.items():
        err = cls("something failed")
        rendered = str(err)
        assert code != "", f"{cls.__name__} has an empty code"
        assert rendered == f"[{code}] something failed", (
            f"{cls.__name__} rendered {rendered!r}, expected "
            f"'[{code}] something failed'"
        )
        assert code in rendered, f"{cls.__name__} str is missing its code"
        assert "something failed" in rendered, (
            f"{cls.__name__} str is missing its message"
        )


def test_class_code_matches_contract() -> None:
    for cls, code in EXPECTED_ERROR_CODES.items():
        assert cls.code == code, (
            f"{cls.__name__}.code == {cls.code!r}, expected {code!r}"
        )


# --- Acceptance criterion 2: registry distinctness + correctness -----------


def test_registry_maps_each_code_to_correct_class() -> None:
    for cls, code in EXPECTED_ERROR_CODES.items():
        assert code in ERROR_REGISTRY, f"registry missing code {code!r}"
        assert ERROR_REGISTRY[code] is cls, (
            f"ERROR_REGISTRY[{code!r}] is {ERROR_REGISTRY[code].__name__}, "
            f"expected {cls.__name__}"
        )


def test_registry_keys_match_contract_exactly() -> None:
    expected_codes = set(EXPECTED_ERROR_CODES.values())
    actual_codes = set(ERROR_REGISTRY.keys())
    missing = expected_codes - actual_codes
    extra = actual_codes - expected_codes
    assert not missing, f"registry missing codes: {missing}"
    assert not extra, f"registry has unexpected codes: {extra}"


def test_registry_is_self_consistent_and_distinct() -> None:
    # Every key maps to a class whose own ``code`` matches the key, and the
    # bound class is genuinely a DepCoverError subclass.
    codes = list(ERROR_REGISTRY.keys())
    assert len(codes) == len(set(codes)), (
        f"duplicate codes present as registry keys: {codes}"
    )
    mapped_classes = list(ERROR_REGISTRY.values())
    mapped_codes = [cls.code for cls in mapped_classes]
    assert len(mapped_codes) == len(set(mapped_codes)), (
        f"classes in the registry do not have pairwise-distinct codes: "
        f"{mapped_codes}"
    )
    for key, cls in ERROR_REGISTRY.items():
        assert cls.code == key, (
            f"registry key {key!r} bound to {cls.__name__} whose code is "
            f"{cls.code!r}"
        )
        assert issubclass(cls, DepCoverError), (
            f"{cls.__name__} in registry is not a DepCoverError subclass"
        )


def test_registry_is_read_only() -> None:
    # A concrete mapping type used at runtime for the registry must reject
    # mutation (it is exposed as a read-only mapping).
    with pytest.raises(TypeError):
        ERROR_REGISTRY["injected"] = DepCoverError  # type: ignore[index]


# --- Acceptance criterion 3: inheritance lattice ---------------------------


def test_inheritance_relationships_hold() -> None:
    assert issubclass(LockfileParseError, IngestError)
    assert issubclass(RateLimitError, GitHubError)
    assert issubclass(SandboxTimeoutError, SandboxError)
    assert issubclass(SandboxUnavailableError, SandboxError)
    assert issubclass(LlmTimeoutError, LlmError)
    assert issubclass(LlmMalformedOutputError, LlmError)
    for cls in ALL_ERROR_CLASSES:
        assert issubclass(cls, DepCoverError), f"{cls.__name__} not a DepCoverError"
        assert issubclass(cls, Exception), f"{cls.__name__} not an Exception"


def test_base_except_clause_catches_subclass() -> None:
    caught: IngestError | None = None
    try:
        raise LockfileParseError("bad lockfile", {"file": "poetry.lock"})
    except IngestError as exc:
        caught = exc
    assert caught is not None, "except IngestError did not catch LockfileParseError"
    assert isinstance(caught, LockfileParseError)
    assert caught.context == {"file": "poetry.lock"}


def test_subclass_not_caught_by_sibling() -> None:
    # A GraphError must not be caught by an IngestError handler.
    with pytest.raises(GraphError):
        try:
            raise GraphError("cycle detected")
        except IngestError:  # pragma: no cover - must not be taken
            pytest.fail("GraphError was wrongly caught as IngestError")


# --- Acceptance criterion 4: context immutability + defensive copy ---------


def test_context_defaults_to_empty_mapping() -> None:
    err = GraphError("no context")
    assert err.context == {}
    assert isinstance(err.context, MappingProxyType)


def test_context_is_a_read_only_mapping() -> None:
    err = GraphError("boom", {"a": "1"})
    assert isinstance(err.context, MappingProxyType)
    with pytest.raises(TypeError):
        err.context["b"] = "2"  # type: ignore[index]


def test_context_is_defensively_copied() -> None:
    seed: dict[str, str] = {"a": "1"}
    err = GraphError("boom", seed)
    # Mutating the original dict after construction must not leak in.
    seed["a"] = "mutated"
    seed["b"] = "new"
    assert err.context == {"a": "1"}, (
        f"context leaked external mutation: {dict(err.context)!r}"
    )


# --- Acceptance criteria 5 & 7: Ok/Err frozen, equal, discriminating -------


def _ok_result() -> Result[int, DepCoverError]:
    """Return an ``Ok`` typed statically as the ``Result`` union.

    Returning the union (rather than assigning an ``Ok`` literal to a local)
    keeps the value from being statically narrowed, so ``isinstance`` and
    ``match`` cross-checks against the other variant stay reachable under
    ``warn_unreachable``.
    """
    return Ok(5)


def _err_result() -> Result[int, DepCoverError]:
    """Return an ``Err`` carrying a ``GraphError``, typed as the union."""
    error: DepCoverError = GraphError("cycle")
    return Err(error)


def test_ok_and_err_are_frozen() -> None:
    ok = Ok(1)
    with pytest.raises(FrozenInstanceError):
        ok.value = 2  # type: ignore[misc]
    err = Err(GraphError("x"))
    with pytest.raises(FrozenInstanceError):
        err.error = GraphError("y")  # type: ignore[misc]


def test_ok_and_err_value_equality() -> None:
    assert Ok(1) == Ok(1)
    assert Ok(1) != Ok(2)
    shared = SandboxError("timed out")
    assert Err(shared) == Err(shared)
    assert Err(SandboxError("a")) != Err(SandboxError("b"))


def test_ok_and_err_discriminate_via_isinstance() -> None:
    ok_value = _ok_result()
    err_value = _err_result()
    # Negative cross-checks first, while each value is still union-typed.
    assert not isinstance(ok_value, Err)
    assert not isinstance(err_value, Ok)
    # Positive checks (these statically narrow, so they come last).
    assert isinstance(ok_value, Ok)
    assert isinstance(err_value, Err)


def test_ok_discriminates_via_structural_match() -> None:
    result = _ok_result()
    matched: str = "unmatched"
    match result:
        case Ok(value=v):
            matched = f"ok:{v}"
        case Err(error=_):
            matched = "err"
    assert matched == "ok:5"


def test_err_discriminates_via_structural_match() -> None:
    result = _err_result()
    matched: str = "unmatched"
    match result:
        case Ok(value=_):
            matched = "ok"
        case Err(error=e):
            matched = f"err:{e.code}"
    assert matched == "err:graph_error"


def test_is_ok_and_is_err_runtime_truth() -> None:
    good = _ok_result()
    bad = _err_result()

    assert is_ok(good) is True
    assert is_ok(bad) is False
    assert is_err(good) is False
    assert is_err(bad) is True


# --- Acceptance criterion 6: static TypeGuard narrowing --------------------


def _static_narrowing(result: Result[int, DepCoverError]) -> str:
    """Prove ``is_ok``/``is_err`` narrow statically.

    The ``assert_type`` calls are the load-bearing assertions here: they are
    runtime no-ops but fail the scoped ``mypy`` run unless the guards narrow
    ``result`` to ``Ok[int]`` (exposing ``.value: int``) and to
    ``Err[DepCoverError]`` (exposing ``.error: DepCoverError``) respectively.
    """
    if is_ok(result):
        assert_type(result.value, int)
        return "ok"
    assert is_err(result)
    assert_type(result.error, DepCoverError)
    return "err"


def test_static_narrowing_helper_runs() -> None:
    assert _static_narrowing(_ok_result()) == "ok"
    assert _static_narrowing(_err_result()) == "err"


# --- Determinism: registry is stable across re-import ----------------------


def test_registry_is_deterministic_across_reload() -> None:
    before = dict(errors_module.ERROR_REGISTRY)
    reload(errors_module)
    after = dict(errors_module.ERROR_REGISTRY)
    assert {code: cls.__name__ for code, cls in after.items()} == {
        code: cls.__name__ for code, cls in before.items()
    }, "ERROR_REGISTRY changed after re-import"
