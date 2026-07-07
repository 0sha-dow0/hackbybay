"""Tests for backend.domain.constants.

Verifies exact Final values, the consensus/degraded panel invariants, the
untrusted-file template well-formedness and rendering, and idempotent
(deterministic) import.
"""

from __future__ import annotations

from importlib import reload
from string import Formatter

import backend.domain.constants as constants_module
from backend.domain.constants import (
    BEHAVIORAL_BATTERY_SIZE,
    CONSENSUS_APPROVALS_REQUIRED,
    CONSENSUS_PANEL_SIZE,
    DEGRADED_APPROVALS_REQUIRED,
    DEGRADED_PANEL_SIZE,
    REPLACEMENT_PACKAGE,
    TARGET_PACKAGE,
    TRANSPLANT_MAX_ATTEMPTS,
    UNTRUSTED_CLOSE,
    UNTRUSTED_OPEN_TEMPLATE,
)

# --- Contract tables -------------------------------------------------------

EXPECTED_STR_CONSTANTS: dict[str, str] = {
    "TARGET_PACKAGE": "axios",
    "REPLACEMENT_PACKAGE": "fetch",
    "UNTRUSTED_OPEN_TEMPLATE": '<untrusted_file path="{path}">',
    "UNTRUSTED_CLOSE": "</untrusted_file>",
}

EXPECTED_INT_CONSTANTS: dict[str, int] = {
    "CONSENSUS_APPROVALS_REQUIRED": 3,
    "CONSENSUS_PANEL_SIZE": 4,
    "DEGRADED_PANEL_SIZE": 2,
    "DEGRADED_APPROVALS_REQUIRED": 2,
    "BEHAVIORAL_BATTERY_SIZE": 10,
    "TRANSPLANT_MAX_ATTEMPTS": 2,
}

ACTUAL_STR_CONSTANTS: dict[str, str] = {
    "TARGET_PACKAGE": TARGET_PACKAGE,
    "REPLACEMENT_PACKAGE": REPLACEMENT_PACKAGE,
    "UNTRUSTED_OPEN_TEMPLATE": UNTRUSTED_OPEN_TEMPLATE,
    "UNTRUSTED_CLOSE": UNTRUSTED_CLOSE,
}

ACTUAL_INT_CONSTANTS: dict[str, int] = {
    "CONSENSUS_APPROVALS_REQUIRED": CONSENSUS_APPROVALS_REQUIRED,
    "CONSENSUS_PANEL_SIZE": CONSENSUS_PANEL_SIZE,
    "DEGRADED_PANEL_SIZE": DEGRADED_PANEL_SIZE,
    "DEGRADED_APPROVALS_REQUIRED": DEGRADED_APPROVALS_REQUIRED,
    "BEHAVIORAL_BATTERY_SIZE": BEHAVIORAL_BATTERY_SIZE,
    "TRANSPLANT_MAX_ATTEMPTS": TRANSPLANT_MAX_ATTEMPTS,
}


# --- Acceptance criterion 5: exact values ----------------------------------


def test_string_constants_match_contract_exactly() -> None:
    for name, expected in EXPECTED_STR_CONSTANTS.items():
        actual = ACTUAL_STR_CONSTANTS[name]
        assert actual == expected, f"{name} == {actual!r}, expected {expected!r}"


def test_int_constants_match_contract_exactly() -> None:
    for name, expected in EXPECTED_INT_CONSTANTS.items():
        actual = ACTUAL_INT_CONSTANTS[name]
        assert actual == expected, f"{name} == {actual!r}, expected {expected!r}"


def test_int_constants_have_int_type_not_bool() -> None:
    # bool is a subclass of int; the contract specifies plain ints.
    for name, actual in ACTUAL_INT_CONSTANTS.items():
        assert type(actual) is int, f"{name} is {type(actual)!r}, expected int"


# --- Acceptance criterion 5: panel/approval invariants ---------------------


def test_consensus_approvals_not_exceeding_panel_size() -> None:
    assert CONSENSUS_APPROVALS_REQUIRED <= CONSENSUS_PANEL_SIZE, (
        f"CONSENSUS_APPROVALS_REQUIRED={CONSENSUS_APPROVALS_REQUIRED} > "
        f"CONSENSUS_PANEL_SIZE={CONSENSUS_PANEL_SIZE}"
    )


def test_degraded_panel_smaller_than_full_panel() -> None:
    assert DEGRADED_PANEL_SIZE < CONSENSUS_PANEL_SIZE, (
        f"DEGRADED_PANEL_SIZE={DEGRADED_PANEL_SIZE} not < "
        f"CONSENSUS_PANEL_SIZE={CONSENSUS_PANEL_SIZE}"
    )


def test_degraded_approvals_not_exceeding_degraded_panel() -> None:
    assert DEGRADED_APPROVALS_REQUIRED <= DEGRADED_PANEL_SIZE, (
        f"DEGRADED_APPROVALS_REQUIRED={DEGRADED_APPROVALS_REQUIRED} > "
        f"DEGRADED_PANEL_SIZE={DEGRADED_PANEL_SIZE}"
    )


# --- Acceptance criterion 6: untrusted-file template -----------------------


def test_template_renders_expected_wrapper() -> None:
    rendered = UNTRUSTED_OPEN_TEMPLATE.format(path="x/y.js")
    assert rendered == '<untrusted_file path="x/y.js">', (
        f"rendered {rendered!r}"
    )


def test_template_has_exactly_one_path_field() -> None:
    field_names = [
        field_name
        for _, field_name, _, _ in Formatter().parse(UNTRUSTED_OPEN_TEMPLATE)
        if field_name is not None
    ]
    assert field_names == ["path"], (
        f"template fields {field_names}, expected exactly ['path']"
    )


def test_template_renders_empty_path() -> None:
    assert UNTRUSTED_OPEN_TEMPLATE.format(path="") == '<untrusted_file path="">'


def test_template_preserves_special_characters_literally() -> None:
    # A path may contain characters that are meaningful to str.format only
    # when unescaped in the template; the substituted value must be verbatim.
    weird_path = "a b/c-d_e.f&g"
    rendered = UNTRUSTED_OPEN_TEMPLATE.format(path=weird_path)
    assert rendered == f'<untrusted_file path="{weird_path}">'


def test_close_tag_matches_open_tag_name() -> None:
    assert UNTRUSTED_CLOSE == "</untrusted_file>"
    assert UNTRUSTED_OPEN_TEMPLATE.startswith("<untrusted_file")


# --- Acceptance criterion 8: deterministic / idempotent import -------------


def _string_snapshot() -> dict[str, str]:
    return {
        "TARGET_PACKAGE": constants_module.TARGET_PACKAGE,
        "REPLACEMENT_PACKAGE": constants_module.REPLACEMENT_PACKAGE,
        "UNTRUSTED_OPEN_TEMPLATE": constants_module.UNTRUSTED_OPEN_TEMPLATE,
        "UNTRUSTED_CLOSE": constants_module.UNTRUSTED_CLOSE,
    }


def _int_snapshot() -> dict[str, int]:
    return {
        "CONSENSUS_APPROVALS_REQUIRED": (
            constants_module.CONSENSUS_APPROVALS_REQUIRED
        ),
        "CONSENSUS_PANEL_SIZE": constants_module.CONSENSUS_PANEL_SIZE,
        "DEGRADED_PANEL_SIZE": constants_module.DEGRADED_PANEL_SIZE,
        "DEGRADED_APPROVALS_REQUIRED": (
            constants_module.DEGRADED_APPROVALS_REQUIRED
        ),
        "BEHAVIORAL_BATTERY_SIZE": constants_module.BEHAVIORAL_BATTERY_SIZE,
        "TRANSPLANT_MAX_ATTEMPTS": constants_module.TRANSPLANT_MAX_ATTEMPTS,
    }


def test_reimport_is_idempotent() -> None:
    strings_before = _string_snapshot()
    ints_before = _int_snapshot()
    reload(constants_module)
    strings_after = _string_snapshot()
    ints_after = _int_snapshot()
    assert strings_after == strings_before, "string constants changed on reload"
    assert ints_after == ints_before, "int constants changed on reload"
