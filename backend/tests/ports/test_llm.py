"""Tests for Unit 11 — the LlmClient port and FakeLlmClientFactory.

Bound to the coder's actual API:

* ``FakeLlmClientFactory(scripted: Mapping[LlmRole, Sequence[LlmResponse]])``.
* ``for_role(role)`` -> ``Ok(client)`` when the role is present in ``scripted``
  (even for an empty sequence), else ``Err(ConfigError)``.
* A client yields its scripted ``LlmResponse`` objects in order; once exhausted
  every further ``complete`` returns ``Err(LlmError)``.
* One client instance per role (a shared cursor across ``for_role`` calls).
* ``LlmMessage.role`` is ``Literal["system", "user"]`` only and ``LlmRequest``
  carries no ``tools``/``functions`` field — a type-level guarantee, asserted
  here by reflection so the check stays runtime- and mypy-clean.
"""

from __future__ import annotations

import dataclasses
from typing import Literal, get_args, get_type_hints

from backend.adapters.fake.fake_llm import FakeLlmClient, FakeLlmClientFactory
from backend.domain.enums import LlmRole
from backend.domain.errors import ConfigError, Err, LlmError, Ok
from backend.ports.llm import (
    LlmClient,
    LlmClientFactory,
    LlmMessage,
    LlmRequest,
    LlmResponse,
)

# --- Fixtures / builders ---------------------------------------------------


def _resp(text: str, finish_reason: Literal["stop", "length"] = "stop") -> LlmResponse:
    return LlmResponse(text=text, model="fake-model-1", finish_reason=finish_reason)


def _req(role: LlmRole = LlmRole.TRANSPLANT) -> LlmRequest:
    return LlmRequest(
        role=role,
        messages=(LlmMessage(role="user", content="hello"),),
        temperature=0.0,
        max_tokens=16,
    )


def _client_for(factory: LlmClientFactory, role: LlmRole) -> LlmClient:
    result = factory.for_role(role)
    assert isinstance(result, Ok), f"expected Ok(client) for {role!r}, got {result!r}"
    return result.value


def _complete_ok(client: LlmClient, request: LlmRequest) -> LlmResponse:
    result = client.complete(request)
    assert isinstance(result, Ok), f"expected Ok(response), got {result!r}"
    return result.value


# --- Case 1: for_role present -> Ok; absent -> Err(ConfigError) -------------


def test_for_role_present_returns_ok_client() -> None:
    factory = FakeLlmClientFactory({LlmRole.TRANSPLANT: (_resp("a"),)})
    result = factory.for_role(LlmRole.TRANSPLANT)
    assert isinstance(result, Ok), f"expected Ok for scripted role, got {result!r}"
    assert LlmClient in type(result.value).__mro__, (
        f"returned value is not an LlmClient: {type(result.value).__name__}"
    )
    assert callable(getattr(result.value, "complete", None))


def test_for_role_absent_returns_config_error() -> None:
    factory = FakeLlmClientFactory({LlmRole.TRANSPLANT: (_resp("a"),)})
    result = factory.for_role(LlmRole.JUDGE_SECURITY)
    assert isinstance(result, Err), f"expected Err for absent role, got {result!r}"
    assert isinstance(result.error, ConfigError), (
        f"expected ConfigError, got {type(result.error).__name__}"
    )
    assert not isinstance(result.error, LlmError), (
        "absent-role failure must be a ConfigError, not an LlmError"
    )


def test_for_role_on_empty_factory_returns_config_error() -> None:
    factory = FakeLlmClientFactory({})
    result = factory.for_role(LlmRole.TRANSPLANT)
    assert isinstance(result, Err), f"expected Err from empty factory, got {result!r}"
    assert isinstance(result.error, ConfigError), (
        f"expected ConfigError, got {type(result.error).__name__}"
    )


# --- Case 2: responses returned IN ORDER across successive calls -----------


def test_complete_returns_scripted_responses_in_order() -> None:
    scripted = (_resp("first"), _resp("second"), _resp("third"))
    factory = FakeLlmClientFactory({LlmRole.TRANSPLANT: scripted})
    client = _client_for(factory, LlmRole.TRANSPLANT)

    observed = [
        _complete_ok(client, _req()).text,
        _complete_ok(client, _req()).text,
        _complete_ok(client, _req()).text,
    ]
    assert observed == ["first", "second", "third"], (
        f"responses not returned in scripted order: {observed}"
    )


def test_complete_returns_exact_scripted_response_object() -> None:
    scripted_resp = _resp("payload")
    factory = FakeLlmClientFactory({LlmRole.TRANSPLANT: (scripted_resp,)})
    client = _client_for(factory, LlmRole.TRANSPLANT)
    returned = _complete_ok(client, _req())
    assert returned == scripted_resp, (
        f"returned response {returned!r} != scripted {scripted_resp!r}"
    )
    assert returned.text == "payload"
    assert returned.model == "fake-model-1"
    assert returned.finish_reason == "stop"


# --- Case 3: exhausting the script -> Err(LlmError) ------------------------


def test_exhausting_script_yields_llm_error() -> None:
    factory = FakeLlmClientFactory({LlmRole.TRANSPLANT: (_resp("only"),)})
    client = _client_for(factory, LlmRole.TRANSPLANT)

    _complete_ok(client, _req())  # consume the single scripted response

    exhausted = client.complete(_req())
    assert isinstance(exhausted, Err), (
        f"expected Err after exhaustion, got {exhausted!r}"
    )
    assert isinstance(exhausted.error, LlmError), (
        f"expected LlmError on exhaustion, got {type(exhausted.error).__name__}"
    )


def test_exhaustion_is_stable_across_repeated_calls() -> None:
    factory = FakeLlmClientFactory({LlmRole.TRANSPLANT: (_resp("only"),)})
    client = _client_for(factory, LlmRole.TRANSPLANT)
    _complete_ok(client, _req())

    for _ in range(3):
        result = client.complete(_req())
        assert isinstance(result, Err), f"exhausted client returned {result!r}"
        assert isinstance(result.error, LlmError), (
            f"expected LlmError, got {type(result.error).__name__}"
        )


# --- Case 4: empty script () -> Ok, first complete -> Err(LlmError) --------


def test_empty_script_for_role_ok_then_first_complete_errors() -> None:
    factory = FakeLlmClientFactory({LlmRole.TRANSPLANT: ()})

    role_result = factory.for_role(LlmRole.TRANSPLANT)
    assert isinstance(role_result, Ok), (
        f"empty script must still yield Ok(client), got {role_result!r}"
    )
    client = role_result.value

    first = client.complete(_req())
    assert isinstance(first, Err), (
        f"first complete on empty script must Err, got {first!r}"
    )
    assert isinstance(first.error, LlmError), (
        f"expected LlmError on empty script, got {type(first.error).__name__}"
    )


# --- Case 5: finish_reason=='length' (truncation) is SURFACED --------------


def test_length_finish_reason_is_surfaced_as_ok() -> None:
    truncated = _resp("partial", finish_reason="length")
    factory = FakeLlmClientFactory({LlmRole.TRANSPLANT: (truncated,)})
    client = _client_for(factory, LlmRole.TRANSPLANT)

    result = client.complete(_req())
    assert isinstance(result, Ok), (
        f"truncated (length) response must come back Ok, not Err: {result!r}"
    )
    assert result.value.finish_reason == "length", (
        f"finish_reason silently altered to {result.value.finish_reason!r}; "
        "'length' must be observable to the caller"
    )
    assert result.value.text == "partial"


def test_stop_finish_reason_is_preserved() -> None:
    factory = FakeLlmClientFactory(
        {LlmRole.TRANSPLANT: (_resp("done", finish_reason="stop"),)}
    )
    client = _client_for(factory, LlmRole.TRANSPLANT)
    result = _complete_ok(client, _req())
    assert result.finish_reason == "stop", (
        f"'stop' mangled to {result.finish_reason!r}"
    )


# --- Case 6: determinism + defensive copy ----------------------------------


def test_identical_factories_yield_identical_sequences() -> None:
    def build() -> LlmClientFactory:
        return FakeLlmClientFactory(
            {LlmRole.TRANSPLANT: (_resp("x1"), _resp("x2", finish_reason="length"))}
        )

    client_a = _client_for(build(), LlmRole.TRANSPLANT)
    client_b = _client_for(build(), LlmRole.TRANSPLANT)

    seq_a = [_complete_ok(client_a, _req()), _complete_ok(client_a, _req())]
    seq_b = [_complete_ok(client_b, _req()), _complete_ok(client_b, _req())]
    assert seq_a == seq_b, (
        f"identical scripts produced diverging sequences: {seq_a} vs {seq_b}"
    )


def test_mutating_source_sequence_after_construction_has_no_effect() -> None:
    first = _resp("first")
    second = _resp("second")
    mutable: list[LlmResponse] = [first, second]
    factory = FakeLlmClientFactory({LlmRole.TRANSPLANT: mutable})

    # Adversarially mutate the caller-owned list after construction.
    mutable.append(_resp("injected-appended"))
    mutable[0] = _resp("injected-overwrite")

    client = _client_for(factory, LlmRole.TRANSPLANT)
    observed = [
        _complete_ok(client, _req()).text,
        _complete_ok(client, _req()).text,
    ]
    assert observed == ["first", "second"], (
        f"defensive copy failed; post-construction mutation leaked in: {observed}"
    )
    # And the appended element must not have extended the script.
    exhausted = client.complete(_req())
    assert isinstance(exhausted, Err), (
        f"appended element leaked into script; expected Err, got {exhausted!r}"
    )
    assert isinstance(exhausted.error, LlmError)


# --- Case 7: per-role independence -----------------------------------------


def test_per_role_cursors_are_independent() -> None:
    factory = FakeLlmClientFactory(
        {
            LlmRole.TRANSPLANT: (_resp("t1"), _resp("t2")),
            LlmRole.JUDGE_SECURITY: (_resp("j1"), _resp("j2")),
        }
    )
    transplant = _client_for(factory, LlmRole.TRANSPLANT)
    security = _client_for(factory, LlmRole.JUDGE_SECURITY)

    # Fully consume the transplant role.
    assert _complete_ok(transplant, _req(LlmRole.TRANSPLANT)).text == "t1"
    assert _complete_ok(transplant, _req(LlmRole.TRANSPLANT)).text == "t2"

    # The security role's cursor must be untouched: it starts at its first item.
    assert _complete_ok(security, _req(LlmRole.JUDGE_SECURITY)).text == "j1", (
        "consuming one role advanced another role's cursor"
    )
    assert _complete_ok(security, _req(LlmRole.JUDGE_SECURITY)).text == "j2"


def test_for_role_returns_same_instance_with_shared_cursor() -> None:
    factory = FakeLlmClientFactory({LlmRole.TRANSPLANT: (_resp("a"), _resp("b"))})
    handle_one = _client_for(factory, LlmRole.TRANSPLANT)
    handle_two = _client_for(factory, LlmRole.TRANSPLANT)
    assert handle_one is handle_two, "for_role must return one instance per role"

    # A response consumed through one handle advances the other's cursor.
    assert _complete_ok(handle_one, _req()).text == "a"
    assert _complete_ok(handle_two, _req()).text == "b", (
        "the two handles do not share a cursor"
    )


# --- Case 8: NEGATIVE TYPE TEST via reflection (no tools / no assistant) ----


def test_llm_request_has_no_tools_or_functions_field() -> None:
    field_names = {field.name for field in dataclasses.fields(LlmRequest)}
    assert "tools" not in field_names, (
        f"LlmRequest exposes a tools field: {sorted(field_names)}"
    )
    assert "functions" not in field_names, (
        f"LlmRequest exposes a functions field: {sorted(field_names)}"
    )
    assert field_names == {"role", "messages", "temperature", "max_tokens"}, (
        f"LlmRequest field set drifted from the tool-less contract: "
        f"{sorted(field_names)}"
    )


def test_llm_message_role_is_system_or_user_only() -> None:
    role_args = get_args(get_type_hints(LlmMessage)["role"])
    assert "assistant" not in role_args, (
        f"LlmMessage.role admits 'assistant': {role_args}"
    )
    assert set(role_args) == {"system", "user"}, (
        f"LlmMessage.role Literal drifted from system/user-only: {role_args}"
    )


def test_llm_message_has_only_role_and_content() -> None:
    field_names = {field.name for field in dataclasses.fields(LlmMessage)}
    assert field_names == {"role", "content"}, (
        f"LlmMessage field set drifted: {sorted(field_names)}"
    )


def test_llm_response_finish_reason_can_only_be_stop_or_length() -> None:
    reason_args = get_args(get_type_hints(LlmResponse)["finish_reason"])
    assert set(reason_args) == {"stop", "length"}, (
        f"finish_reason Literal drifted; truncation may be unrepresentable: "
        f"{reason_args}"
    )


# --- Structural sanity: frozen immutability of the wire types ---------------


def test_wire_types_are_frozen_dataclasses() -> None:
    for datatype in (LlmMessage, LlmRequest, LlmResponse):
        params = getattr(datatype, "__dataclass_params__", None)
        assert params is not None, f"{datatype.__name__} is not a dataclass"
        assert params.frozen, f"{datatype.__name__} is not frozen"


def test_fake_client_is_an_llm_client() -> None:
    client = FakeLlmClient(LlmRole.TRANSPLANT, (_resp("a"),))
    assert LlmClient in type(client).__mro__, "FakeLlmClient is not an LlmClient"
    assert LlmClientFactory in FakeLlmClientFactory.__mro__, (
        "FakeLlmClientFactory is not an LlmClientFactory"
    )
