"""Tests for backend.services.normalizer (Unit 24 — behavioral-diff normalizer).

Covers the §12.4 contract: equal-modulo-noise-and-order inputs normalize to
byte-identical strings; noise keys (exact-token, case/separator-insensitive)
are stripped at every depth while non-noise keys survive; object keys are
sorted recursively; arrays preserve order; bool/int are kept distinct;
malformed JSON yields an ``Ok`` with whitespace-canonicalized text (never an
``Err``); the only ``Err`` is a blank/whitespace ``case_id``; and the whole
operation is idempotent and deterministic.

Signature under test (coder deviation from the plan): ``case_id`` comes FIRST.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import pytest

from backend.domain.errors import DepCoverError, Err, Ok, Result
from backend.domain.models import JsonValue, NormalizedOutput
from backend.services.normalizer import normalize_json, normalize_output

# --------------------------------------------------------------------------- #
# Narrowing helpers (Result / JsonValue) — engineering-bar isinstance narrowing #
# --------------------------------------------------------------------------- #


def _ok(result: Result[NormalizedOutput, DepCoverError]) -> NormalizedOutput:
    assert isinstance(result, Ok), f"expected Ok, got {result!r}"
    return result.value


def _err(result: Result[NormalizedOutput, DepCoverError]) -> DepCoverError:
    assert isinstance(result, Err), f"expected Err, got {result!r}"
    return result.error


def _norm(case_id: str, raw: str) -> str:
    return _ok(normalize_output(case_id, raw)).normalized


def _as_mapping(value: JsonValue) -> Mapping[str, JsonValue]:
    assert isinstance(value, Mapping), f"expected Mapping, got {type(value).__name__}"
    return value


def _as_list(value: JsonValue) -> Sequence[JsonValue]:
    assert isinstance(value, Sequence) and not isinstance(value, str), (
        f"expected non-str Sequence, got {type(value).__name__}"
    )
    return value


_CID = "case-42"


# --------------------------------------------------------------------------- #
# Case 1 — §12.4: differ only in timestamp / requestId value + key order       #
# --------------------------------------------------------------------------- #


def test_timestamp_and_request_id_and_key_order_normalize_identically() -> None:
    a = (
        '{"timestamp":"2020-01-01T00:00:00Z","requestId":"abc-111",'
        '"userId":42,"data":[1,2,3]}'
    )
    b = (
        '{"data":[1,2,3],"userId":42,"requestId":"zzz-999",'
        '"timestamp":"2099-12-31T23:59:59Z"}'
    )
    na, nb = _norm(_CID, a), _norm(_CID, b)
    assert na == nb, f"expected identical normalization; a={na!r} b={nb!r}"
    # Byte-exact expectation: noise stripped, keys sorted, compact separators.
    assert na == '{"data":[1,2,3],"userId":42}'


def test_noise_value_type_change_does_not_affect_normalization() -> None:
    # The stripped noise field differs in both value AND JSON type across inputs.
    a = '{"id":"a-string-id","keep":1}'
    b = '{"id":123456789,"keep":1}'
    assert _norm(_CID, a) == _norm(_CID, b) == '{"keep":1}'


def test_nested_equal_modulo_noise_and_order_is_byte_identical() -> None:
    a = (
        '{"z":{"traceId":"t1","createdAt":"2020","inner":{"time":"09:00","v":1}},'
        '"a":[{"id":1,"n":"x"},{"requestId":"r","n":"y"}],"userId":7}'
    )
    b = (
        '{"userId":7,"a":[{"n":"x","id":9},{"n":"y","requestId":"other"}],'
        '"z":{"createdAt":"2099","inner":{"v":1,"time":"23:59"},"traceId":"t2"}}'
    )
    na, nb = _norm(_CID, a), _norm(_CID, b)
    assert na == nb, f"nested mod-noise mismatch: a={na!r} b={nb!r}"
    assert na == '{"a":[{"n":"x"},{"n":"y"}],"userId":7,"z":{"inner":{"v":1}}}'


# --------------------------------------------------------------------------- #
# Case 2 — Idempotence on the representative battery                           #
# --------------------------------------------------------------------------- #

_IDEMPOTENCE_BATTERY: tuple[str, ...] = (
    # JSON object with noise + unsorted keys
    '{"timestamp":"2020","requestId":"r","userId":1,"data":[3,2,1]}',
    # Nested object
    '{"b":{"id":9,"k":{"traceId":"t","v":2}},"a":1}',
    # Array (order preserved)
    "[3,1,2,10,-5]",
    # Array of objects
    '[{"id":1,"z":2},{"createdAt":"x","a":3}]',
    # Malformed text
    "not json { oops",
    # Plain text with collapsible whitespace
    "hello    world\n\tagain",
    # Empty string
    "",
    # Whitespace only
    "   \t\n  ",
    # Bare JSON scalars
    "true",
    "false",
    "null",
    "42",
    '"just a quoted string"',
    # Deeply nested noise
    '{"a":{"b":{"c":{"id":1,"keep":2,"updatedAt":"z"}}}}',
)


@pytest.mark.parametrize("raw", _IDEMPOTENCE_BATTERY)
def test_normalize_output_is_idempotent(raw: str) -> None:
    once = _norm(_CID, raw)
    twice = _norm(_CID, once)
    assert twice == once, f"not a fixed point for {raw!r}: {once!r} -> {twice!r}"


@pytest.mark.parametrize("raw", _IDEMPOTENCE_BATTERY)
def test_normalize_output_is_idempotent_three_passes(raw: str) -> None:
    o1 = _norm(_CID, raw)
    o2 = _norm(_CID, o1)
    o3 = _norm(_CID, o2)
    assert o1 == o2 == o3, f"drift across passes for {raw!r}: {o1!r},{o2!r},{o3!r}"


# --------------------------------------------------------------------------- #
# Case 3 — Malformed JSON => Ok (deterministic text normalization), not Err     #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("not json { oops", "not json { oops"),
        ("not   json {   oops", "not json { oops"),
        ("  leading and trailing  ", "leading and trailing"),
        ('{"unterminated": ', '{"unterminated":'),
        ("{'single':'quotes'}", "{'single':'quotes'}"),
        ("[1,2,", "[1,2,"),
        ("", ""),
        ("   \t\n  ", ""),
    ],
)
def test_malformed_json_is_ok_with_canonicalized_text(raw: str, expected: str) -> None:
    result = normalize_output(_CID, raw)
    assert isinstance(result, Ok), f"malformed input must be Ok, got {result!r}"
    assert result.value.normalized == expected, (
        f"raw={raw!r} expected={expected!r} actual={result.value.normalized!r}"
    )


def test_malformed_json_reparses_after_whitespace_collapse() -> None:
    # Form-feed is not JSON whitespace, so the raw parse fails; collapsing
    # whitespace makes it valid, exercising the reparse branch -> compact JSON.
    result = normalize_output(_CID, '{"a": 1}\f')
    assert isinstance(result, Ok)
    assert result.value.normalized == '{"a":1}'


# --------------------------------------------------------------------------- #
# Case 4 — Blank/whitespace case_id => Err; valid case_id => Ok                 #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("blank_cid", ["", "   ", "\t", "\n", " \t\n "])
def test_blank_case_id_is_err(blank_cid: str) -> None:
    result = normalize_output(blank_cid, '{"ok":1}')
    error = _err(result)
    assert isinstance(error, DepCoverError)


@pytest.mark.parametrize("cid", ["c", "0", "case-42", " padded ", "  x"])
def test_non_blank_case_id_is_ok_and_preserved(cid: str) -> None:
    out = _ok(normalize_output(cid, '{"ok":1}'))
    assert out.case_id == cid
    assert out.normalized == '{"ok":1}'


def test_blank_case_id_takes_priority_even_over_malformed_raw() -> None:
    # case_id validation happens regardless of raw payload shape.
    assert isinstance(normalize_output("", "not json { oops"), Err)


# --------------------------------------------------------------------------- #
# Case 5 — Non-noise keys preserved; only exact-token noise keys stripped       #
# --------------------------------------------------------------------------- #


def test_user_id_survives_but_id_is_stripped() -> None:
    value: JsonValue = {"userId": 1, "id": 2}
    result = _as_mapping(normalize_json(value))
    assert "userId" in result, "userId must survive (substring match forbidden)"
    assert "id" not in result, "exact-token 'id' must be stripped"


@pytest.mark.parametrize(
    "survivor",
    ["userId", "candidate", "width", "hidden", "identifier", "timestamps", "validate", "idx"],
)
def test_non_noise_keys_survive(survivor: str) -> None:
    value: JsonValue = {survivor: 1, "id": 99}
    result = _as_mapping(normalize_json(value))
    assert survivor in result, f"{survivor!r} must not be treated as a noise key"


@pytest.mark.parametrize(
    "noise",
    [
        "timestamp",
        "time",
        "datetime",
        "date",
        "id",
        "requestId",
        "request_id",
        "Request-Id",
        "correlationId",
        "correlation_id",
        "traceId",
        "createdAt",
        "created_at",
        "updatedAt",
        "UPDATED_AT",
        "DateTime",
    ],
)
def test_noise_keys_are_stripped_case_and_separator_insensitive(noise: str) -> None:
    value: JsonValue = {noise: "drop me", "keep": 1}
    result = _as_mapping(normalize_json(value))
    assert noise not in result, f"{noise!r} should be stripped"
    assert result == {"keep": 1}


# --------------------------------------------------------------------------- #
# Case 6 — normalize_json: recursive sort + strip, order-stable arrays, bool/int #
# --------------------------------------------------------------------------- #


def test_object_keys_sorted_recursively() -> None:
    value: JsonValue = {"c": 1, "a": {"z": 1, "m": 2, "b": 3}, "b": 2}
    result = _as_mapping(normalize_json(value))
    assert list(result.keys()) == ["a", "b", "c"]
    inner = _as_mapping(result["a"])
    assert list(inner.keys()) == ["b", "m", "z"]


def test_noise_stripped_at_every_depth_including_inside_arrays() -> None:
    value: JsonValue = {
        "id": 0,
        "outer": {
            "traceId": "t",
            "items": [
                {"id": 1, "keep": "a", "createdAt": "x"},
                {"requestId": "r", "keep": "b"},
            ],
            "kept": 5,
        },
    }
    result = normalize_json(value)
    assert result == {"outer": {"items": [{"keep": "a"}, {"keep": "b"}], "kept": 5}}


def test_arrays_preserve_order() -> None:
    value: JsonValue = [3, 1, 2, 10, -5, 0]
    result = _as_list(normalize_json(value))
    assert list(result) == [3, 1, 2, 10, -5, 0]


def test_array_of_objects_preserves_order_but_sorts_inner_keys() -> None:
    value: JsonValue = [{"b": 1, "a": 2}, {"y": 3, "x": 4}]
    result = _as_list(normalize_json(value))
    first = _as_mapping(result[0])
    second = _as_mapping(result[1])
    assert list(first.keys()) == ["a", "b"]
    assert list(second.keys()) == ["x", "y"]
    # Element order must NOT be reordered.
    assert first["a"] == 2 and second["x"] == 4


def test_bool_vs_int_distinction_preserved_in_normalize_json() -> None:
    value: JsonValue = {"flag_true": True, "num_one": 1, "flag_false": False, "num_zero": 0}
    result = _as_mapping(normalize_json(value))
    assert type(result["flag_true"]) is bool
    assert type(result["num_one"]) is int
    assert type(result["flag_false"]) is bool
    assert type(result["num_zero"]) is int
    assert result["flag_true"] is True
    assert result["flag_false"] is False


def test_bool_vs_int_distinction_survives_serialization() -> None:
    assert _norm(_CID, '{"x":true}') == '{"x":true}'
    assert _norm(_CID, '{"x":1}') == '{"x":1}'
    assert _norm(_CID, '{"x":true}') != _norm(_CID, '{"x":1}')


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("plain string", "plain string"),
        (42, 42),
        (3.5, 3.5),
        (True, True),
        (False, False),
        (None, None),
        ([], []),
        ({}, {}),
    ],
)
def test_normalize_json_scalar_and_empty_identities(
    value: JsonValue, expected: JsonValue
) -> None:
    result = normalize_json(value)
    assert result == expected
    assert type(result) is type(expected)


# --------------------------------------------------------------------------- #
# Case 7 — Determinism: same input => identical output across calls             #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("raw", _IDEMPOTENCE_BATTERY)
def test_normalize_output_deterministic(raw: str) -> None:
    outputs = {_norm(_CID, raw) for _ in range(5)}
    assert len(outputs) == 1, f"non-deterministic output for {raw!r}: {outputs!r}"


def test_normalize_json_deterministic() -> None:
    value: JsonValue = {
        "requestId": "r",
        "b": {"z": 1, "a": {"id": 1, "k": 2}},
        "a": [3, 1, 2],
        "timestamp": "now",
    }
    results = [normalize_json(value) for _ in range(5)]
    assert all(r == results[0] for r in results)
    assert results[0] == {"a": [3, 1, 2], "b": {"a": {"k": 2}, "z": 1}}


def test_normalize_json_does_not_mutate_input() -> None:
    value: dict[str, JsonValue] = {"id": 1, "keep": 2}
    _ = normalize_json(value)
    assert value == {"id": 1, "keep": 2}, "input mapping must not be mutated"
