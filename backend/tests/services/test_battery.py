"""Tests for backend.services.battery (Unit 25).

Covers the deterministic 10-input behavioral battery and the golden-outputs
loader contract:

- ``input_battery()``: exactly ``BEHAVIORAL_BATTERY_SIZE`` cases, unique ids,
  the 404/500/malformed-JSON semantic-gap probes present, every request a
  Mapping carrying method/path, and identity-stable / order-stable determinism.
- ``load_golden_outputs()``: Ok iff the source keys are a bijection with the
  battery ids, returns an immutable id->NormalizedOutput mapping that echoes the
  supplied (already-normalized) values verbatim, and Err (naming missing/extra
  ids) otherwise.
- ``BehavioralCase`` / ``NormalizedOutput`` frozen contract.
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from typing import cast

import pytest
from pydantic import ValidationError

from backend.domain.constants import BEHAVIORAL_BATTERY_SIZE
from backend.domain.errors import DepCoverError, Err, Ok, Result
from backend.domain.models import BehavioralCase, NormalizedOutput
from backend.services.battery import input_battery, load_golden_outputs

# --- Contract tables -------------------------------------------------------

EXPECTED_BATTERY_IDS: tuple[str, ...] = (
    "get-success",
    "get-not-found",
    "get-server-error",
    "get-malformed-json",
    "get-empty-body",
    "get-large-payload",
    "post-special-chars",
    "get-slow-timeout",
    "get-query-params",
    "post-create-resource",
)

REQUIRED_CATEGORIES: frozenset[str] = frozenset({"404", "500", "malformed-JSON"})


# --- Helpers ---------------------------------------------------------------


def _battery_ids() -> list[str]:
    return [case.id for case in input_battery()]


def _full_source() -> dict[str, str]:
    """A golden source covering exactly the battery ids (bijection)."""
    return {case.id: f"normalized::{case.id}" for case in input_battery()}


def _ok_value(
    result: Result[Mapping[str, NormalizedOutput], DepCoverError],
) -> Mapping[str, NormalizedOutput]:
    assert isinstance(result, Ok), f"expected Ok, got {result!r}"
    return result.value


def _err_error(
    result: Result[Mapping[str, NormalizedOutput], DepCoverError],
) -> DepCoverError:
    assert isinstance(result, Err), f"expected Err, got {result!r}"
    return result.error


def _attempt_setitem(
    mapping: MutableMapping[str, NormalizedOutput],
    key: str,
    value: NormalizedOutput,
) -> None:
    mapping[key] = value


# --- input_battery(): size / uniqueness / categories -----------------------


def test_battery_size_equals_constant() -> None:
    battery = input_battery()
    assert len(battery) == BEHAVIORAL_BATTERY_SIZE, (
        f"battery has {len(battery)} cases, expected {BEHAVIORAL_BATTERY_SIZE}"
    )
    assert BEHAVIORAL_BATTERY_SIZE == 10, (
        f"BEHAVIORAL_BATTERY_SIZE == {BEHAVIORAL_BATTERY_SIZE}, expected 10"
    )


def test_battery_ids_are_unique() -> None:
    ids = _battery_ids()
    assert len(ids) == len(set(ids)), f"duplicate ids present: {ids}"


def test_battery_ids_match_expected_order() -> None:
    ids = tuple(_battery_ids())
    assert ids == EXPECTED_BATTERY_IDS, (
        f"battery ids {ids}, expected {EXPECTED_BATTERY_IDS}"
    )


def test_required_semantic_gap_categories_present() -> None:
    categories = {case.category for case in input_battery()}
    missing = REQUIRED_CATEGORIES - categories
    assert not missing, (
        f"required categories missing: {sorted(missing)}; present: {sorted(categories)}"
    )


def test_every_case_is_behavioral_case_with_string_fields() -> None:
    for case in input_battery():
        assert isinstance(case, BehavioralCase), f"{case!r} is not BehavioralCase"
        assert isinstance(case.id, str) and case.id, f"bad id: {case.id!r}"
        assert isinstance(case.category, str) and case.category, (
            f"bad category for {case.id!r}: {case.category!r}"
        )
        assert isinstance(case.description, str) and case.description, (
            f"bad description for {case.id!r}: {case.description!r}"
        )


def test_every_request_is_mapping_with_method_and_path() -> None:
    for case in input_battery():
        request = case.request
        assert isinstance(request, Mapping), (
            f"request for {case.id!r} is {type(request)!r}, expected Mapping"
        )
        assert "method" in request, f"request for {case.id!r} missing 'method'"
        assert "path" in request, f"request for {case.id!r} missing 'path'"
        method = request["method"]
        path = request["path"]
        assert isinstance(method, str) and method, (
            f"method for {case.id!r} is {method!r}"
        )
        assert isinstance(path, str) and path.startswith("/"), (
            f"path for {case.id!r} is {path!r}, expected leading '/'"
        )


# --- input_battery(): determinism / identity / order stability -------------


def test_input_battery_returns_identity_stable_object() -> None:
    first = input_battery()
    second = input_battery()
    assert first is second, "input_battery() returned distinct objects across calls"


def test_input_battery_is_a_tuple() -> None:
    assert isinstance(input_battery(), tuple), (
        f"input_battery() returned {type(input_battery())!r}, expected tuple"
    )


def test_input_battery_equal_and_order_stable_across_calls() -> None:
    first = input_battery()
    second = input_battery()
    assert first == second, "battery not equal across calls"
    assert [c.id for c in first] == [c.id for c in second], "battery order not stable"


# --- load_golden_outputs(): happy path -------------------------------------


def test_load_golden_happy_path_is_ok() -> None:
    source = _full_source()
    result = load_golden_outputs(source)
    assert isinstance(result, Ok), f"expected Ok for full bijection source, got {result!r}"


def test_load_golden_keys_cover_exactly_battery_ids() -> None:
    goldens = _ok_value(load_golden_outputs(_full_source()))
    assert set(goldens) == set(EXPECTED_BATTERY_IDS), (
        f"golden keys {sorted(goldens)}, expected {sorted(EXPECTED_BATTERY_IDS)}"
    )


def test_load_golden_values_echo_source_verbatim() -> None:
    source = _full_source()
    goldens = _ok_value(load_golden_outputs(source))
    for case_id, expected_norm in source.items():
        entry = goldens[case_id]
        assert isinstance(entry, NormalizedOutput), (
            f"value for {case_id!r} is {type(entry)!r}, expected NormalizedOutput"
        )
        assert entry.case_id == case_id, (
            f"NormalizedOutput.case_id == {entry.case_id!r}, expected {case_id!r}"
        )
        assert entry.normalized == expected_norm, (
            f"normalized for {case_id!r} == {entry.normalized!r}, "
            f"expected {expected_norm!r}"
        )


def test_load_golden_does_not_renormalize_values() -> None:
    # The loader takes ALREADY-normalized goldens; it must echo raw bytes,
    # including empty strings, whitespace, unicode and markup, unchanged.
    tricky = {
        "get-success": "",
        "get-not-found": "   leading and trailing   ",
        "get-server-error": 'héllo — 日本語 "q" & <tags>\n\ttab',
        "get-malformed-json": "{not: valid, json",
    }
    source = _full_source()
    source.update(tricky)
    goldens = _ok_value(load_golden_outputs(source))
    for case_id, raw in tricky.items():
        assert goldens[case_id].normalized == raw, (
            f"loader altered value for {case_id!r}: "
            f"{goldens[case_id].normalized!r} != {raw!r}"
        )


def test_load_golden_result_mapping_is_immutable() -> None:
    goldens = _ok_value(load_golden_outputs(_full_source()))
    view = cast(MutableMapping[str, NormalizedOutput], goldens)
    with pytest.raises(TypeError):
        _attempt_setitem(view, "get-success", NormalizedOutput(case_id="x", normalized="y"))
    # The rejected mutation must not have altered the mapping.
    assert goldens["get-success"].case_id == "get-success"


def test_load_golden_is_deterministic() -> None:
    first = _ok_value(load_golden_outputs(_full_source()))
    second = _ok_value(load_golden_outputs(_full_source()))
    assert dict(first) == dict(second), "load_golden_outputs not deterministic"


# --- load_golden_outputs(): missing / extra / malformed --------------------


def test_load_golden_missing_one_id_is_err() -> None:
    source = _full_source()
    del source["get-not-found"]
    error = _err_error(load_golden_outputs(source))
    assert isinstance(error, DepCoverError), f"error is {type(error)!r}"
    assert "get-not-found" in error.context["missing_ids"], (
        f"missing_ids={error.context['missing_ids']!r} does not name 'get-not-found'"
    )
    assert error.context["extra_ids"] == "", (
        f"extra_ids should be empty, got {error.context['extra_ids']!r}"
    )


def test_load_golden_extra_id_is_err() -> None:
    source = _full_source()
    source["not-a-battery-case"] = "junk"
    error = _err_error(load_golden_outputs(source))
    assert "not-a-battery-case" in error.context["extra_ids"], (
        f"extra_ids={error.context['extra_ids']!r} does not name 'not-a-battery-case'"
    )
    assert error.context["missing_ids"] == "", (
        f"missing_ids should be empty, got {error.context['missing_ids']!r}"
    )


def test_load_golden_missing_and_extra_reported_together() -> None:
    source = _full_source()
    del source["get-server-error"]
    source["ghost-id"] = "junk"
    error = _err_error(load_golden_outputs(source))
    assert "get-server-error" in error.context["missing_ids"], (
        f"missing_ids={error.context['missing_ids']!r} omits 'get-server-error'"
    )
    assert "ghost-id" in error.context["extra_ids"], (
        f"extra_ids={error.context['extra_ids']!r} omits 'ghost-id'"
    )


def test_load_golden_empty_source_is_err_listing_all_ids() -> None:
    error = _err_error(load_golden_outputs({}))
    missing = error.context["missing_ids"]
    for expected_id in EXPECTED_BATTERY_IDS:
        assert expected_id in missing, (
            f"missing_ids={missing!r} omits {expected_id!r}"
        )
    assert error.context["extra_ids"] == "", (
        f"extra_ids should be empty for empty source, got {error.context['extra_ids']!r}"
    )


def test_load_golden_error_context_is_immutable_mapping() -> None:
    error = _err_error(load_golden_outputs({}))
    assert isinstance(error.context, Mapping), (
        f"context is {type(error.context)!r}, expected Mapping"
    )
    mutable = cast(MutableMapping[str, str], error.context)
    with pytest.raises(TypeError):
        mutable["missing_ids"] = "tampered"


def test_load_golden_off_by_one_short_source_is_err() -> None:
    # Exactly one fewer id than the battery: an off-by-one under-coverage.
    source = _full_source()
    dropped = EXPECTED_BATTERY_IDS[-1]
    del source[dropped]
    assert len(source) == BEHAVIORAL_BATTERY_SIZE - 1
    error = _err_error(load_golden_outputs(source))
    assert dropped in error.context["missing_ids"]


def test_load_golden_error_is_deterministic() -> None:
    first = _err_error(load_golden_outputs({}))
    second = _err_error(load_golden_outputs({}))
    assert dict(first.context) == dict(second.context), (
        "error context not deterministic across calls"
    )


# --- Frozen-model contract -------------------------------------------------


def test_behavioral_case_is_frozen() -> None:
    case = input_battery()[0]
    original_id = case.id
    with pytest.raises((ValidationError, TypeError)):
        case.id = "mutated"
    assert case.id == original_id, f"frozen BehavioralCase was mutated: {case.id!r}"


def test_normalized_output_is_frozen() -> None:
    entry = NormalizedOutput(case_id="c", normalized="n")
    with pytest.raises((ValidationError, TypeError)):
        entry.normalized = "mutated"
    assert entry.normalized == "n", f"frozen NormalizedOutput was mutated: {entry.normalized!r}"
