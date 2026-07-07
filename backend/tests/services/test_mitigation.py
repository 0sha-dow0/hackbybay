"""Tests for backend.services.mitigation.MitigationService (Unit 20).

Binds every test to the real parsing contract in services/mitigation.py:

* the model must return a single JSON object whose top-level keys are exactly
  the four StrategyKind values (upgrade, shim, transplant, accept_risk);
* each strategy value must be an object with exactly the string fields
  title, effort, blast_radius, residual_risk, rationale;
* only TRANSPLANT is executable;
* options(incident_id, report, cve_summary) threads incident_id first and
  stamps it onto the card set (never report.id);
* for_role(MITIGATION) absent -> Err(ConfigError), not re-wrapped as LlmError;
* finish_reason == "length" and any malformed/duplicate/wrong-field JSON ->
  Err(LlmMalformedOutputError);
* markdown code fences are stripped before parsing.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Literal

import pytest

from backend.adapters.fake.fake_llm import FakeLlmClientFactory
from backend.domain.enums import GraphNodeKind, LlmRole, StrategyKind
from backend.domain.errors import (
    ConfigError,
    DepCoverError,
    Err,
    LlmError,
    LlmMalformedOutputError,
    Ok,
    Result,
)
from backend.domain.models import (
    CentralityScore,
    GraphLayout,
    GraphLayoutNode,
    LockfileWarning,
    MitigationCardSet,
    UnderwritingReport,
)
from backend.ports.llm import LlmResponse
from backend.services.mitigation import MitigationService

_INCIDENT_ID = "incident-XYZ"
_CVE_SUMMARY = "CVE-2026-0001: prototype pollution in left-pad."


# --------------------------------------------------------------------------- #
# Builders bound to the real schema.
# --------------------------------------------------------------------------- #
def _card(title: str) -> dict[str, str]:
    return {
        "title": title,
        "effort": "medium",
        "blast_radius": "12 files, 3 call sites affected",
        "residual_risk": "2 failing tests may remain",
        "rationale": "grounded in the supplied evidence",
    }


def _valid_payload() -> dict[str, dict[str, str]]:
    return {
        "upgrade": _card("Upgrade to the patched release"),
        "shim": _card("Insert a compatibility shim"),
        "transplant": _card("Transplant a maintained replacement"),
        "accept_risk": _card("Accept the residual risk"),
    }


def _valid_json() -> str:
    return json.dumps(_valid_payload())


def _response(
    text: str, finish_reason: Literal["stop", "length"] = "stop"
) -> LlmResponse:
    return LlmResponse(text=text, model="fake-mitigation-model", finish_reason=finish_reason)


def _service(response: LlmResponse) -> MitigationService:
    factory = FakeLlmClientFactory({LlmRole.MITIGATION: (response,)})
    return MitigationService(factory)


def _service_without_mitigation_role() -> MitigationService:
    return MitigationService(FakeLlmClientFactory({}))


def _report(
    *,
    report_id: str = "report-different-id",
    failing_tests: tuple[str, ...] = ("test_pad_left", "test_pad_unicode"),
) -> UnderwritingReport:
    return UnderwritingReport(
        id=report_id,
        repo_id="repo-1",
        target_package="left-pad",
        failing_tests=failing_tests,
        affected_file_count=3,
        centrality=(CentralityScore(package="left-pad", score=0.42),),
        graph_layout=GraphLayout(
            nodes=(
                GraphLayoutNode(
                    id="n1", x=0.0, y=0.0, kind=GraphNodeKind.PACKAGE, label="left-pad"
                ),
                GraphLayoutNode(
                    id="n2", x=1.0, y=1.0, kind=GraphNodeKind.CALL_SITE, label="pad()"
                ),
            ),
            edges=(),
        ),
        warnings=(LockfileWarning(shape="peer", reason="version mismatch"),),
        created_at=datetime(2026, 7, 7, tzinfo=timezone.utc),
    )


def _ok(result: Result[MitigationCardSet, DepCoverError]) -> MitigationCardSet:
    assert isinstance(result, Ok), f"expected Ok, got {result!r}"
    return result.value


def _err(result: Result[MitigationCardSet, DepCoverError]) -> DepCoverError:
    assert isinstance(result, Err), f"expected Err, got {result!r}"
    return result.error


def _raw_duplicate_strategy() -> str:
    card = json.dumps(_card("dup"))
    return (
        "{"
        f'"upgrade": {card}, '
        f'"upgrade": {card}, '
        f'"shim": {card}, '
        f'"transplant": {card}, '
        f'"accept_risk": {card}'
        "}"
    )


def _malformed_texts() -> list[tuple[str, str]]:
    cases: list[tuple[str, str]] = [
        ("not_json", "not json"),
        ("empty_string", ""),
        ("json_array", "[1, 2, 3]"),
        ("json_number", "42"),
        ("json_string_scalar", '"just a string"'),
        ("json_null", "null"),
        ("duplicate_strategy_key", _raw_duplicate_strategy()),
    ]

    missing = {k: v for k, v in _valid_payload().items() if k != "accept_risk"}
    cases.append(("missing_strategy", json.dumps(missing)))

    extra: dict[str, object] = {k: v for k, v in _valid_payload().items()}
    extra["rollback"] = _card("extra strategy")
    cases.append(("extra_strategy", json.dumps(extra)))

    wrong_name: dict[str, object] = {k: v for k, v in _valid_payload().items()}
    del wrong_name["shim"]
    wrong_name["patch"] = _card("renamed strategy")
    cases.append(("renamed_strategy", json.dumps(wrong_name)))

    card_missing_field: dict[str, object] = {k: v for k, v in _valid_payload().items()}
    trimmed = {k: v for k, v in _card("x").items() if k != "rationale"}
    card_missing_field["upgrade"] = trimmed
    cases.append(("card_missing_field", json.dumps(card_missing_field)))

    card_extra_field: dict[str, object] = {k: v for k, v in _valid_payload().items()}
    padded: dict[str, object] = {k: v for k, v in _card("x").items()}
    padded["surprise"] = "unexpected"
    card_extra_field["upgrade"] = padded
    cases.append(("card_extra_field", json.dumps(card_extra_field)))

    card_not_object: dict[str, object] = {k: v for k, v in _valid_payload().items()}
    card_not_object["shim"] = "not an object"
    cases.append(("card_not_object", json.dumps(card_not_object)))

    card_field_not_string: dict[str, object] = {k: v for k, v in _valid_payload().items()}
    numeric: dict[str, object] = {k: v for k, v in _card("x").items()}
    numeric["effort"] = 5
    card_field_not_string["transplant"] = numeric
    cases.append(("card_field_not_string", json.dumps(card_field_not_string)))

    return cases


_MALFORMED = _malformed_texts()


# --------------------------------------------------------------------------- #
# 1 + 8. Happy path + determinism.
# --------------------------------------------------------------------------- #
def test_happy_path_returns_four_cards_one_per_kind() -> None:
    result = _service(_response(_valid_json())).options(
        _INCIDENT_ID, _report(), _CVE_SUMMARY
    )

    card_set = _ok(result)
    assert card_set.incident_id == _INCIDENT_ID
    assert len(card_set.options) == 4
    assert {option.kind for option in card_set.options} == set(StrategyKind)


def test_happy_path_only_transplant_is_executable() -> None:
    card_set = _ok(
        _service(_response(_valid_json())).options(_INCIDENT_ID, _report(), _CVE_SUMMARY)
    )

    for option in card_set.options:
        assert option.executable == (option.kind is StrategyKind.TRANSPLANT)
    executable_kinds = [o.kind for o in card_set.options if o.executable]
    assert executable_kinds == [StrategyKind.TRANSPLANT]


def test_happy_path_blast_radius_and_residual_risk_non_empty() -> None:
    card_set = _ok(
        _service(_response(_valid_json())).options(_INCIDENT_ID, _report(), _CVE_SUMMARY)
    )

    for option in card_set.options:
        assert isinstance(option.blast_radius, str) and option.blast_radius != ""
        assert isinstance(option.residual_risk, str) and option.residual_risk != ""


def test_determinism_identical_inputs_yield_identical_card_set() -> None:
    report = _report()

    first = _service(_response(_valid_json())).options(_INCIDENT_ID, report, _CVE_SUMMARY)
    second = _service(_response(_valid_json())).options(_INCIDENT_ID, report, _CVE_SUMMARY)

    left = _ok(first)
    right = _ok(second)
    assert left == right
    assert left.options == right.options


# --------------------------------------------------------------------------- #
# 2. incident_id threading.
# --------------------------------------------------------------------------- #
def test_incident_id_is_threaded_not_report_id() -> None:
    report = _report(report_id="report-different-id")
    assert report.id != _INCIDENT_ID

    card_set = _ok(
        _service(_response(_valid_json())).options(_INCIDENT_ID, report, _CVE_SUMMARY)
    )

    assert card_set.incident_id == _INCIDENT_ID
    assert card_set.incident_id != report.id


# --------------------------------------------------------------------------- #
# 3. for_role(MITIGATION) absent -> ConfigError, not LlmError.
# --------------------------------------------------------------------------- #
def test_missing_mitigation_role_is_config_error() -> None:
    result = _service_without_mitigation_role().options(
        _INCIDENT_ID, _report(), _CVE_SUMMARY
    )

    error = _err(result)
    assert isinstance(error, ConfigError)
    assert error.code == "config_error"
    assert not isinstance(error, LlmError)


# --------------------------------------------------------------------------- #
# 4. finish_reason == "length" -> LlmMalformedOutputError.
# --------------------------------------------------------------------------- #
def test_truncated_response_is_malformed_output_error() -> None:
    # Text is otherwise perfectly valid; truncation alone must fail it.
    result = _service(_response(_valid_json(), finish_reason="length")).options(
        _INCIDENT_ID, _report(), _CVE_SUMMARY
    )

    error = _err(result)
    assert isinstance(error, LlmMalformedOutputError)


# --------------------------------------------------------------------------- #
# 5. Malformed / duplicate / wrong-field JSON -> LlmMalformedOutputError.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("case_id", "text"), _MALFORMED, ids=[case_id for case_id, _ in _MALFORMED]
)
def test_malformed_json_is_malformed_output_error(case_id: str, text: str) -> None:
    result = _service(_response(text)).options(_INCIDENT_ID, _report(), _CVE_SUMMARY)

    error = _err(result)
    assert isinstance(error, LlmMalformedOutputError), f"case={case_id}: {error!r}"


# --------------------------------------------------------------------------- #
# 6. Markdown-fenced valid JSON -> still Ok.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("case_id", "wrapped"),
    [
        ("fenced_json_lang", "```json\n" + _valid_json() + "\n```"),
        ("fenced_bare", "```\n" + _valid_json() + "\n```"),
        ("fenced_with_padding", "  ```json\n" + _valid_json() + "\n```  \n"),
    ],
    ids=["fenced_json_lang", "fenced_bare", "fenced_with_padding"],
)
def test_markdown_fenced_json_is_ok(case_id: str, wrapped: str) -> None:
    card_set = _ok(
        _service(_response(wrapped)).options(_INCIDENT_ID, _report(), _CVE_SUMMARY)
    )

    assert card_set.incident_id == _INCIDENT_ID
    assert {option.kind for option in card_set.options} == set(StrategyKind)


# --------------------------------------------------------------------------- #
# 7. Empty failing-test evidence -> still Ok with all four cards.
# --------------------------------------------------------------------------- #
def test_empty_failing_tests_still_produces_full_card_set() -> None:
    report = _report(failing_tests=())
    assert report.failing_tests == ()

    card_set = _ok(
        _service(_response(_valid_json())).options(_INCIDENT_ID, report, _CVE_SUMMARY)
    )

    assert len(card_set.options) == 4
    assert {option.kind for option in card_set.options} == set(StrategyKind)
    accept_risk = [o for o in card_set.options if o.kind is StrategyKind.ACCEPT_RISK]
    assert len(accept_risk) == 1
